from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from stream_kernel.execution.transport.secure_tcp_transport import (
    BindPolicyError,
    InvalidSignatureError,
    MissingSignatureError,
    ReplayNonceError,
    SecureEnvelope,
    SecureTcpConfig,
    SecureTcpTransport,
    TimestampExpiredError,
    UnsupportedKindError,
    WirePayloadTooLargeError,
)


def _transport(*, now: int = 1_000_000, max_payload_bytes: int = 4096) -> SecureTcpTransport:
    return SecureTcpTransport(
        SecureTcpConfig(
            bind_host="127.0.0.1",
            bind_port=0,
            secret=b"phase1-secret",
            ttl_seconds=30,
            nonce_cache_size=128,
            max_payload_bytes=max_payload_bytes,
            allowed_kinds={"request", "response", "event"},
        ),
        now_fn=lambda: now,
    )


def test_secure_tcp_accepts_valid_signed_message() -> None:
    # SEC-IPC-01: valid signed message accepted.
    transport = _transport()
    message = transport.sign_envelope(kind="request", payload_bytes=b"hello", trace_id="t1")

    decoded = transport.decode_framed_message(transport.encode_framed_message(message))

    assert decoded.kind == "request"
    assert decoded.payload_bytes == b"hello"
    assert decoded.trace_id == "t1"


def test_secure_tcp_roundtrip_preserves_payload_and_metadata() -> None:
    # SEC-IPC-01b: send/receive over localhost TCP preserves message bytes/metadata.
    transport = _transport()
    try:
        listener = transport.open_listener()
    except PermissionError:
        pytest.skip("AF_INET socket creation is not permitted in this sandbox")
    host, port = listener.getsockname()
    assert host == "127.0.0.1"

    received: list[SecureEnvelope] = []
    errors: list[Exception] = []

    def _recv() -> None:
        try:
            received.append(transport.receive_once(listener))
        except Exception as exc:  # pragma: no cover - test harness path
            errors.append(exc)

    thread = threading.Thread(target=_recv)
    thread.start()
    try:
        message = transport.sign_envelope(
            kind="request",
            payload_bytes=b"line-1",
            trace_id="run:1",
            target="parse",
            headers={"content-type": "text/plain"},
        )
        transport.send_once("127.0.0.1", port, message)
    finally:
        thread.join(timeout=2)
        listener.close()

    assert not errors
    assert len(received) == 1
    msg = received[0]
    assert msg.payload_bytes == b"line-1"
    assert msg.target == "parse"
    assert msg.trace_id == "run:1"
    assert msg.headers == {"content-type": "text/plain"}


def test_secure_tcp_rejects_invalid_signature() -> None:
    # SEC-IPC-02: invalid signature rejected.
    transport = _transport()
    message = transport.sign_envelope(kind="request", payload_bytes=b"hello")
    tampered = replace(message, sig="deadbeef")

    with pytest.raises(InvalidSignatureError):
        transport.decode_framed_message(transport.encode_framed_message(tampered))


def test_secure_tcp_rejects_missing_signature() -> None:
    # SEC-IPC-02b: missing signature rejected.
    transport = _transport()
    message = transport.sign_envelope(kind="request", payload_bytes=b"hello")

    with pytest.raises(MissingSignatureError):
        transport.decode_framed_message(transport.encode_framed_message(replace(message, sig="")))


def test_secure_tcp_rejects_expired_timestamp() -> None:
    # SEC-IPC-03: expired timestamp rejected.
    transport = _transport(now=100)
    message = transport.sign_envelope(kind="request", payload_bytes=b"hello", ts=10)

    with pytest.raises(TimestampExpiredError):
        transport.decode_framed_message(transport.encode_framed_message(message))


def test_secure_tcp_rejects_replayed_nonce() -> None:
    # SEC-IPC-04: replay nonce rejected.
    transport = _transport()
    message = transport.sign_envelope(kind="request", payload_bytes=b"hello", nonce="n-1")
    framed = transport.encode_framed_message(message)

    decoded = transport.decode_framed_message(framed)
    assert decoded.payload_bytes == b"hello"

    with pytest.raises(ReplayNonceError):
        transport.decode_framed_message(framed)


def test_secure_tcp_rejects_oversized_wire_payload_pre_decode() -> None:
    # SEC-IPC-05: oversized payload rejected pre-decode.
    transport = _transport(max_payload_bytes=8)
    oversized_wire = (9).to_bytes(4, byteorder="big", signed=False) + b"x" * 9

    with pytest.raises(WirePayloadTooLargeError):
        transport.decode_framed_message(oversized_wire)


def test_secure_tcp_rejects_unsupported_kind() -> None:
    # SEC-IPC-06: unsupported kind rejected.
    transport = _transport()
    message = transport.sign_envelope(kind="internal.debug", payload_bytes=b"x")

    with pytest.raises(UnsupportedKindError):
        transport.decode_framed_message(transport.encode_framed_message(message))


def test_secure_tcp_rejects_non_localhost_bind_host() -> None:
    # SEC-IPC-07: local profile rejects non-localhost bind host.
    with pytest.raises(BindPolicyError):
        SecureTcpConfig(
            bind_host="0.0.0.0",
            bind_port=0,
            secret=b"s",
            ttl_seconds=30,
            nonce_cache_size=8,
            max_payload_bytes=1024,
            allowed_kinds={"request"},
        )


def test_secure_tcp_rejects_invalid_bind_port() -> None:
    # SEC-IPC-07b: invalid bind port rejected by transport setup.
    with pytest.raises(BindPolicyError):
        SecureTcpConfig(
            bind_host="127.0.0.1",
            bind_port=70_000,
            secret=b"s",
            ttl_seconds=30,
            nonce_cache_size=8,
            max_payload_bytes=1024,
            allowed_kinds={"request"},
        )
