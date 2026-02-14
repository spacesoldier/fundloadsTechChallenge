from __future__ import annotations

import json

import pytest

from stream_kernel.execution.transport.bootstrap_keys import build_bootstrap_key_bundle
from stream_kernel.execution.transport.control_plane import (
    CONTROL_KIND_ACK,
    CONTROL_KIND_BOOTSTRAP_BUNDLE,
    ControlMessage,
    ControlPlaneChannel,
    ControlPlaneDuplicateBootstrapError,
    ControlPlaneSession,
    ControlPlaneStartBeforeReadyError,
    bootstrap_bundle_from_wire,
    bootstrap_bundle_message,
    bootstrap_bundle_to_wire,
    ready_message,
    start_work_message,
)
from stream_kernel.execution.transport.secure_tcp_transport import (
    InvalidSignatureError,
    ReplayNonceError,
    SecureTcpConfig,
    SecureTcpTransport,
    TimestampExpiredError,
)


def _channel(*, now: int = 1_000_000) -> ControlPlaneChannel:
    transport = SecureTcpTransport(
        SecureTcpConfig(
            bind_host="127.0.0.1",
            bind_port=0,
            secret=b"phase5pre-control-secret",
            ttl_seconds=30,
            nonce_cache_size=128,
            max_payload_bytes=8192,
            allowed_kinds=ControlPlaneChannel.allowed_kinds(),
        ),
        now_fn=lambda: now,
    )
    return ControlPlaneChannel(transport=transport)


def _tamper_framed(framed: bytes, **updates: object) -> bytes:
    declared = int.from_bytes(framed[:4], byteorder="big", signed=False)
    payload = framed[4:]
    assert declared == len(payload)
    wire = json.loads(payload.decode("utf-8"))
    assert isinstance(wire, dict)
    wire.update(updates)
    mutated_payload = json.dumps(wire, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return len(mutated_payload).to_bytes(4, byteorder="big", signed=False) + mutated_payload


def _runtime_with_generated_secret() -> dict[str, object]:
    return {
        "platform": {
            "execution_ipc": {
                "auth": {
                    "secret_mode": "generated",
                    "kdf": "hkdf_sha256",
                }
            }
        }
    }


def test_p5pre_ctrl_01_bootstrap_bundle_roundtrip_and_ack() -> None:
    # P5PRE-CTRL-01: bundle command should roundtrip through control channel and produce ACK.
    channel = _channel()
    session = ControlPlaneSession()
    bundle = build_bootstrap_key_bundle(
        _runtime_with_generated_secret(),
        token_bytes_fn=lambda n: b"s" * n,
        now_fn=lambda: 123,
    )
    outbound = bootstrap_bundle_message(
        correlation_id="c1",
        worker_id="execution.cpu#1",
        bundle=bundle,
    )

    inbound = channel.decode_framed_message(channel.encode_framed_message(outbound))
    ack = session.process(inbound)

    assert inbound.kind == CONTROL_KIND_BOOTSTRAP_BUNDLE
    assert ack.kind == CONTROL_KIND_ACK
    assert ack.worker_id == "execution.cpu#1"
    assert ack.correlation_id == "c1"
    assert ack.payload.get("ack_kind") == CONTROL_KIND_BOOTSTRAP_BUNDLE


def test_p5pre_ctrl_02_duplicate_bootstrap_command_is_rejected() -> None:
    # P5PRE-CTRL-02: second bootstrap for the same worker is deterministic protocol error.
    channel = _channel()
    session = ControlPlaneSession()
    bundle = build_bootstrap_key_bundle(
        _runtime_with_generated_secret(),
        token_bytes_fn=lambda n: b"k" * n,
        now_fn=lambda: 777,
    )
    first = bootstrap_bundle_message(correlation_id="c1", worker_id="execution.cpu#1", bundle=bundle)
    second = bootstrap_bundle_message(correlation_id="c2", worker_id="execution.cpu#1", bundle=bundle)

    _ = session.process(channel.decode_framed_message(channel.encode_framed_message(first)))
    with pytest.raises(ControlPlaneDuplicateBootstrapError):
        session.process(channel.decode_framed_message(channel.encode_framed_message(second)))


def test_p5pre_ctrl_03_control_channel_rejects_invalid_signature_ttl_and_replay() -> None:
    # P5PRE-CTRL-03: control channel must reject invalid signature, expired ttl and replayed nonce.
    bundle = build_bootstrap_key_bundle(
        _runtime_with_generated_secret(),
        token_bytes_fn=lambda n: b"z" * n,
        now_fn=lambda: 42,
    )

    invalid_sig_channel = _channel()
    outbound = bootstrap_bundle_message(correlation_id="c1", worker_id="w1", bundle=bundle)
    framed = invalid_sig_channel.encode_framed_message(outbound)
    tampered_sig = _tamper_framed(framed, sig="deadbeef")
    with pytest.raises(InvalidSignatureError):
        invalid_sig_channel.decode_framed_message(tampered_sig)

    expired_channel = _channel(now=100)
    expired_framed = expired_channel.encode_framed_message(outbound)
    tampered_ts = _tamper_framed(expired_framed, ts=1)
    with pytest.raises(TimestampExpiredError):
        expired_channel.decode_framed_message(tampered_ts)

    replay_channel = _channel()
    replay_framed = replay_channel.encode_framed_message(outbound)
    _ = replay_channel.decode_framed_message(replay_framed)
    with pytest.raises(ReplayNonceError):
        replay_channel.decode_framed_message(replay_framed)


def test_p5pre_ctrl_04_start_work_is_gated_by_ready_handshake() -> None:
    # P5PRE-CTRL-04: start_work before READY is rejected; after READY it is acknowledged.
    session = ControlPlaneSession()
    blocked = start_work_message(correlation_id="c-start-1", required_workers=["execution.cpu#1"])
    with pytest.raises(ControlPlaneStartBeforeReadyError):
        session.process(blocked)

    channel = _channel()
    bundle = build_bootstrap_key_bundle(
        _runtime_with_generated_secret(),
        token_bytes_fn=lambda n: b"q" * n,
        now_fn=lambda: 999,
    )
    bootstrap = bootstrap_bundle_message(correlation_id="c-bootstrap", worker_id="execution.cpu#1", bundle=bundle)
    ready = ready_message(correlation_id="c-ready", worker_id="execution.cpu#1")
    _ = session.process(channel.decode_framed_message(channel.encode_framed_message(bootstrap)))
    _ = session.process(channel.decode_framed_message(channel.encode_framed_message(ready)))

    ack = session.process(start_work_message(correlation_id="c-start-2", required_workers=["execution.cpu#1"]))
    assert ack.kind == CONTROL_KIND_ACK
    assert ack.payload.get("ack_kind") == "control.start_work"


def test_p5pre_ctrl_05_bootstrap_bundle_wire_roundtrip_preserves_secrets() -> None:
    # P5PRE-CTRL-05: bootstrap bundle wire mapper should preserve key material bytes exactly.
    original = build_bootstrap_key_bundle(
        _runtime_with_generated_secret(),
        token_bytes_fn=lambda n: b"m" * n,
        now_fn=lambda: 555,
    )
    wire = bootstrap_bundle_to_wire(original)
    restored = bootstrap_bundle_from_wire(wire)
    assert restored.created_at_epoch == 555
    assert restored.execution_ipc.secret_mode == original.execution_ipc.secret_mode
    assert restored.execution_ipc.kdf == original.execution_ipc.kdf
    assert restored.execution_ipc.master_secret == original.execution_ipc.master_secret
    assert restored.execution_ipc.signing_secret == original.execution_ipc.signing_secret


def test_control_message_requires_non_empty_correlation_id() -> None:
    # Control message constructor should keep deterministic message identity contract.
    with pytest.raises(ValueError):
        ControlMessage(kind="control.ready", correlation_id="", worker_id="w1", payload={})
