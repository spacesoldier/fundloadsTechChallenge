from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from stream_kernel.execution.transport.bootstrap_keys import (
    BootstrapKeyBundle,
    ExecutionIpcKeyMaterial,
)
from stream_kernel.execution.transport.secure_tcp_transport import (
    SecureEnvelope,
    SecureTcpTransport,
)

CONTROL_KIND_BOOTSTRAP_BUNDLE = "control.bootstrap_bundle"
CONTROL_KIND_READY = "control.ready"
CONTROL_KIND_HEARTBEAT = "control.heartbeat"
CONTROL_KIND_START_WORK = "control.start_work"
CONTROL_KIND_STOP = "control.stop"
CONTROL_KIND_ACK = "control.ack"

CONTROL_ALLOWED_KINDS = frozenset(
    {
        CONTROL_KIND_BOOTSTRAP_BUNDLE,
        CONTROL_KIND_READY,
        CONTROL_KIND_HEARTBEAT,
        CONTROL_KIND_START_WORK,
        CONTROL_KIND_STOP,
        CONTROL_KIND_ACK,
    }
)


class ControlPlaneError(RuntimeError):
    # Base control-plane contract error.
    pass


class ControlPlaneProtocolError(ControlPlaneError):
    # Control-plane message shape/state violation.
    pass


class ControlPlaneDuplicateBootstrapError(ControlPlaneProtocolError):
    # Duplicate bootstrap command for the same worker.
    pass


class ControlPlaneStartBeforeReadyError(ControlPlaneProtocolError):
    # Start-work command arrived before required READY handshakes.
    pass


@dataclass(frozen=True, slots=True)
class ControlMessage:
    kind: str
    correlation_id: str
    worker_id: str | None
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("ControlMessage.kind must be a non-empty string")
        if not isinstance(self.correlation_id, str) or not self.correlation_id:
            raise ValueError("ControlMessage.correlation_id must be a non-empty string")
        if self.worker_id is not None and (not isinstance(self.worker_id, str) or not self.worker_id):
            raise ValueError("ControlMessage.worker_id must be null or non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("ControlMessage.payload must be a mapping")


def bootstrap_bundle_to_wire(bundle: BootstrapKeyBundle) -> dict[str, object]:
    return {
        "created_at_epoch": bundle.created_at_epoch,
        "execution_ipc": {
            "secret_mode": bundle.execution_ipc.secret_mode,
            "kdf": bundle.execution_ipc.kdf,
            "master_secret_b64": base64.b64encode(bundle.execution_ipc.master_secret).decode("ascii"),
            "signing_secret_b64": base64.b64encode(bundle.execution_ipc.signing_secret).decode("ascii"),
        },
    }


def bootstrap_bundle_from_wire(wire: dict[str, object]) -> BootstrapKeyBundle:
    if not isinstance(wire, dict):
        raise ControlPlaneProtocolError("bootstrap bundle payload must be a mapping")
    created_at = wire.get("created_at_epoch")
    if not isinstance(created_at, int):
        raise ControlPlaneProtocolError("bootstrap bundle created_at_epoch must be an integer")
    execution_ipc_wire = wire.get("execution_ipc")
    if not isinstance(execution_ipc_wire, dict):
        raise ControlPlaneProtocolError("bootstrap bundle execution_ipc must be a mapping")

    secret_mode = execution_ipc_wire.get("secret_mode")
    if not isinstance(secret_mode, str) or not secret_mode:
        raise ControlPlaneProtocolError("bootstrap bundle execution_ipc.secret_mode must be a non-empty string")
    kdf = execution_ipc_wire.get("kdf")
    if not isinstance(kdf, str) or not kdf:
        raise ControlPlaneProtocolError("bootstrap bundle execution_ipc.kdf must be a non-empty string")

    master_b64 = execution_ipc_wire.get("master_secret_b64")
    signing_b64 = execution_ipc_wire.get("signing_secret_b64")
    if not isinstance(master_b64, str) or not master_b64:
        raise ControlPlaneProtocolError("bootstrap bundle execution_ipc.master_secret_b64 must be non-empty")
    if not isinstance(signing_b64, str) or not signing_b64:
        raise ControlPlaneProtocolError("bootstrap bundle execution_ipc.signing_secret_b64 must be non-empty")

    try:
        master_secret = base64.b64decode(master_b64.encode("ascii"), validate=True)
        signing_secret = base64.b64decode(signing_b64.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ControlPlaneProtocolError("bootstrap bundle contains invalid base64 key material") from exc

    return BootstrapKeyBundle(
        created_at_epoch=created_at,
        execution_ipc=ExecutionIpcKeyMaterial(
            secret_mode=secret_mode,
            kdf=kdf,
            master_secret=master_secret,
            signing_secret=signing_secret,
        ),
    )


def bootstrap_bundle_message(
    *,
    correlation_id: str,
    worker_id: str,
    bundle: BootstrapKeyBundle,
) -> ControlMessage:
    return ControlMessage(
        kind=CONTROL_KIND_BOOTSTRAP_BUNDLE,
        correlation_id=correlation_id,
        worker_id=worker_id,
        payload={"bundle": bootstrap_bundle_to_wire(bundle)},
    )


def ready_message(*, correlation_id: str, worker_id: str) -> ControlMessage:
    return ControlMessage(
        kind=CONTROL_KIND_READY,
        correlation_id=correlation_id,
        worker_id=worker_id,
        payload={},
    )


def start_work_message(*, correlation_id: str, required_workers: list[str] | None = None) -> ControlMessage:
    normalized_required = list(required_workers or [])
    return ControlMessage(
        kind=CONTROL_KIND_START_WORK,
        correlation_id=correlation_id,
        worker_id=None,
        payload={"required_workers": normalized_required},
    )


class ControlPlaneChannel:
    # Control-plane codec over SecureTcpTransport envelope signing/framing.
    def __init__(self, *, transport: SecureTcpTransport) -> None:
        self._transport = transport

    @staticmethod
    def allowed_kinds() -> set[str]:
        return set(CONTROL_ALLOWED_KINDS)

    def encode_framed_message(self, message: ControlMessage) -> bytes:
        if message.kind not in CONTROL_ALLOWED_KINDS:
            raise ControlPlaneProtocolError(f"unsupported control message kind: {message.kind}")
        payload_bytes = json.dumps(
            {
                "correlation_id": message.correlation_id,
                "worker_id": message.worker_id,
                "payload": message.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        envelope = self._transport.sign_envelope(
            kind=message.kind,
            payload_bytes=payload_bytes,
            target=message.worker_id,
        )
        return self._transport.encode_framed_message(envelope)

    def decode_framed_message(self, framed: bytes) -> ControlMessage:
        envelope = self._transport.decode_framed_message(framed)
        return self._envelope_to_message(envelope)

    def _envelope_to_message(self, envelope: SecureEnvelope) -> ControlMessage:
        if envelope.kind not in CONTROL_ALLOWED_KINDS:
            raise ControlPlaneProtocolError(f"unsupported control message kind: {envelope.kind}")
        try:
            payload = json.loads(envelope.payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControlPlaneProtocolError("control message payload must be valid utf-8 json") from exc
        if not isinstance(payload, dict):
            raise ControlPlaneProtocolError("control message payload must be a mapping")

        correlation_id = payload.get("correlation_id")
        if not isinstance(correlation_id, str) or not correlation_id:
            raise ControlPlaneProtocolError("control message correlation_id must be a non-empty string")

        worker_id_raw = payload.get("worker_id")
        if worker_id_raw is not None and (not isinstance(worker_id_raw, str) or not worker_id_raw):
            raise ControlPlaneProtocolError("control message worker_id must be null or non-empty string")
        worker_id_from_target = envelope.target if isinstance(envelope.target, str) and envelope.target else None
        if worker_id_from_target is not None and worker_id_raw is not None and worker_id_from_target != worker_id_raw:
            raise ControlPlaneProtocolError("worker_id mismatch between envelope target and payload")
        worker_id = worker_id_raw or worker_id_from_target

        message_payload = payload.get("payload", {})
        if not isinstance(message_payload, dict):
            raise ControlPlaneProtocolError("control message payload field must be a mapping")
        return ControlMessage(
            kind=envelope.kind,
            correlation_id=correlation_id,
            worker_id=worker_id,
            payload=message_payload,
        )


class ControlPlaneSession:
    # Deterministic control session state machine for bootstrap/readiness/start-work gating.
    def __init__(self) -> None:
        self._bootstrapped_workers: set[str] = set()
        self._ready_workers: set[str] = set()

    def process(self, message: ControlMessage) -> ControlMessage:
        if message.kind == CONTROL_KIND_BOOTSTRAP_BUNDLE:
            return self._process_bootstrap_bundle(message)
        if message.kind == CONTROL_KIND_READY:
            return self._process_ready(message)
        if message.kind == CONTROL_KIND_START_WORK:
            return self._process_start_work(message)
        if message.kind in {CONTROL_KIND_HEARTBEAT, CONTROL_KIND_STOP}:
            return self._ack(message)
        raise ControlPlaneProtocolError(f"unsupported control message kind in session: {message.kind}")

    def _process_bootstrap_bundle(self, message: ControlMessage) -> ControlMessage:
        if message.worker_id is None:
            raise ControlPlaneProtocolError("bootstrap_bundle requires worker_id")
        bundle_raw = message.payload.get("bundle")
        if not isinstance(bundle_raw, dict):
            raise ControlPlaneProtocolError("bootstrap_bundle payload must include bundle mapping")
        # Validate wire shape deterministically.
        _ = bootstrap_bundle_from_wire(bundle_raw)
        if message.worker_id in self._bootstrapped_workers:
            raise ControlPlaneDuplicateBootstrapError(
                f"bootstrap bundle already accepted for worker '{message.worker_id}'"
            )
        self._bootstrapped_workers.add(message.worker_id)
        return self._ack(message)

    def _process_ready(self, message: ControlMessage) -> ControlMessage:
        if message.worker_id is None:
            raise ControlPlaneProtocolError("ready requires worker_id")
        if message.worker_id not in self._bootstrapped_workers:
            raise ControlPlaneProtocolError(f"ready received before bootstrap for worker '{message.worker_id}'")
        self._ready_workers.add(message.worker_id)
        return self._ack(message)

    def _process_start_work(self, message: ControlMessage) -> ControlMessage:
        required_workers_raw = message.payload.get("required_workers", [])
        if not isinstance(required_workers_raw, list):
            raise ControlPlaneProtocolError("start_work required_workers must be a list when provided")
        if not all(isinstance(item, str) and item for item in required_workers_raw):
            raise ControlPlaneProtocolError("start_work required_workers entries must be non-empty strings")
        required_workers = list(required_workers_raw)
        missing = [worker_id for worker_id in required_workers if worker_id not in self._ready_workers]
        if missing:
            raise ControlPlaneStartBeforeReadyError(
                f"start_work received before required workers are ready: {sorted(missing)}"
            )
        return self._ack(message)

    @staticmethod
    def _ack(message: ControlMessage) -> ControlMessage:
        return ControlMessage(
            kind=CONTROL_KIND_ACK,
            correlation_id=message.correlation_id,
            worker_id=message.worker_id,
            payload={"ack_kind": message.kind},
        )
