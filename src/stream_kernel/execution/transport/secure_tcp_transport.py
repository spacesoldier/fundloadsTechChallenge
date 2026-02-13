from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import socket
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable


class SecureTcpTransportError(ValueError):
    # Base transport error for deterministic caller-side handling.
    pass


class BindPolicyError(SecureTcpTransportError):
    # Bind/listen policy violation for local TCP profile.
    pass


class MissingSignatureError(SecureTcpTransportError):
    # Signed envelope has no signature.
    pass


class InvalidSignatureError(SecureTcpTransportError):
    # HMAC signature mismatch.
    pass


class TimestampExpiredError(SecureTcpTransportError):
    # Envelope timestamp is outside allowed TTL window.
    pass


class ReplayNonceError(SecureTcpTransportError):
    # Envelope nonce was already accepted in the replay guard cache.
    pass


class WirePayloadTooLargeError(SecureTcpTransportError):
    # Framed payload length exceeds configured max before decode.
    pass


class UnsupportedKindError(SecureTcpTransportError):
    # Envelope kind is not in allowed set.
    pass


@dataclass(frozen=True, slots=True)
class SecureTcpConfig:
    bind_host: str
    bind_port: int
    secret: bytes
    ttl_seconds: int
    nonce_cache_size: int
    max_payload_bytes: int
    allowed_kinds: set[str] | frozenset[str]

    def __post_init__(self) -> None:
        if self.bind_host != "127.0.0.1":
            raise BindPolicyError("bind_host must be 127.0.0.1 for tcp_local profile")
        if not isinstance(self.bind_port, int) or self.bind_port < 0 or self.bind_port > 65535:
            raise BindPolicyError("bind_port must be in range [0, 65535]")
        if not isinstance(self.secret, bytes) or not self.secret:
            raise BindPolicyError("secret must be non-empty bytes")
        if not isinstance(self.ttl_seconds, int) or self.ttl_seconds <= 0:
            raise BindPolicyError("ttl_seconds must be > 0")
        if not isinstance(self.nonce_cache_size, int) or self.nonce_cache_size <= 0:
            raise BindPolicyError("nonce_cache_size must be > 0")
        if not isinstance(self.max_payload_bytes, int) or self.max_payload_bytes <= 0:
            raise BindPolicyError("max_payload_bytes must be > 0")
        if not self.allowed_kinds:
            raise BindPolicyError("allowed_kinds must not be empty")
        if not all(isinstance(kind, str) and kind for kind in self.allowed_kinds):
            raise BindPolicyError("allowed_kinds entries must be non-empty strings")
        object.__setattr__(self, "allowed_kinds", frozenset(self.allowed_kinds))


@dataclass(frozen=True, slots=True)
class SecureEnvelope:
    trace_id: str | None
    reply_to: str | None
    kind: str
    target: str | list[str] | None
    payload_bytes: bytes
    headers: dict[str, str]
    ts: int
    nonce: str
    sig: str


class _ReplayGuard:
    # Fixed-size replay guard: rejects duplicate (nonce, ts) keys in acceptance window.
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._queue: deque[tuple[str, int]] = deque()
        self._seen: set[tuple[str, int]] = set()

    def accept(self, nonce: str, ts: int) -> None:
        key = (nonce, ts)
        if key in self._seen:
            raise ReplayNonceError("replayed nonce detected")
        self._queue.append(key)
        self._seen.add(key)
        if len(self._queue) > self._capacity:
            oldest = self._queue.popleft()
            self._seen.remove(oldest)


class SecureTcpTransport:
    # Secure localhost TCP transport with framed payloads and envelope auth checks.
    def __init__(
        self,
        config: SecureTcpConfig,
        *,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self._now_fn = now_fn or (lambda: int(time.time()))
        self._replay = _ReplayGuard(config.nonce_cache_size)

    def sign_envelope(
        self,
        *,
        kind: str,
        payload_bytes: bytes,
        trace_id: str | None = None,
        reply_to: str | None = None,
        target: str | list[str] | None = None,
        headers: dict[str, str] | None = None,
        ts: int | None = None,
        nonce: str | None = None,
    ) -> SecureEnvelope:
        envelope = SecureEnvelope(
            trace_id=trace_id,
            reply_to=reply_to,
            kind=kind,
            target=target,
            payload_bytes=payload_bytes,
            headers=dict(headers or {}),
            ts=int(self._now_fn() if ts is None else ts),
            nonce=nonce or secrets.token_hex(16),
            sig="",
        )
        sig = self._sign_canonical(envelope)
        return replace_sig(envelope, sig)

    def open_listener(self) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.config.bind_host, self.config.bind_port))
        listener.listen()
        return listener

    def send_once(self, host: str, port: int, envelope: SecureEnvelope) -> None:
        if host != "127.0.0.1":
            raise BindPolicyError("send host must be 127.0.0.1 for tcp_local profile")
        if not isinstance(port, int) or port <= 0 or port > 65535:
            raise BindPolicyError("send port must be in range [1, 65535]")
        payload = self.encode_framed_message(envelope)
        with socket.create_connection((host, port), timeout=5) as conn:
            conn.sendall(payload)

    def receive_once(self, listener: socket.socket) -> SecureEnvelope:
        conn, _ = listener.accept()
        with conn:
            framed = self._read_framed_message(conn)
        return self.decode_framed_message(framed)

    def encode_framed_message(self, envelope: SecureEnvelope) -> bytes:
        payload = json.dumps(self._wire_dict(envelope), sort_keys=True, separators=(",", ":")).encode("utf-8")
        size = len(payload)
        return size.to_bytes(4, byteorder="big", signed=False) + payload

    def decode_framed_message(self, framed: bytes) -> SecureEnvelope:
        if len(framed) < 4:
            raise SecureTcpTransportError("framed message must contain 4-byte length prefix")
        declared = int.from_bytes(framed[:4], byteorder="big", signed=False)
        if declared > self.config.max_payload_bytes:
            raise WirePayloadTooLargeError("framed payload exceeds max_payload_bytes")
        payload = framed[4:]
        if len(payload) != declared:
            raise SecureTcpTransportError("framed payload length does not match prefix")
        return self._decode_payload(payload)

    def _decode_payload(self, payload: bytes) -> SecureEnvelope:
        wire = self._decode_wire_json(payload)

        sig = wire.get("sig")
        if not isinstance(sig, str) or not sig:
            raise MissingSignatureError("missing signature")

        kind = wire.get("kind")
        if not isinstance(kind, str) or not kind:
            raise SecureTcpTransportError("kind must be a non-empty string")
        if kind not in self.config.allowed_kinds:
            raise UnsupportedKindError(f"unsupported kind: {kind}")

        ts = wire.get("ts")
        if not isinstance(ts, int):
            raise SecureTcpTransportError("ts must be an integer")
        now = int(self._now_fn())
        if abs(now - ts) > self.config.ttl_seconds:
            raise TimestampExpiredError("timestamp outside ttl window")

        nonce = wire.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise SecureTcpTransportError("nonce must be a non-empty string")

        envelope = self._wire_to_envelope(wire)
        expected = self._sign_canonical(replace_sig(envelope, ""))
        if not hmac.compare_digest(sig, expected):
            raise InvalidSignatureError("invalid signature")

        self._replay.accept(nonce=nonce, ts=ts)
        return envelope

    def _read_framed_message(self, conn: socket.socket) -> bytes:
        header = self._read_exact(conn, 4)
        declared = int.from_bytes(header, byteorder="big", signed=False)
        if declared > self.config.max_payload_bytes:
            raise WirePayloadTooLargeError("framed payload exceeds max_payload_bytes")
        payload = self._read_exact(conn, declared)
        return header + payload

    @staticmethod
    def _read_exact(conn: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = conn.recv(remaining)
            if not chunk:
                raise SecureTcpTransportError("unexpected EOF while reading framed payload")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _wire_dict(self, envelope: SecureEnvelope) -> dict[str, object]:
        return {
            "trace_id": envelope.trace_id,
            "reply_to": envelope.reply_to,
            "kind": envelope.kind,
            "target": envelope.target,
            "payload_b64": base64.b64encode(envelope.payload_bytes).decode("ascii"),
            "headers": envelope.headers,
            "ts": envelope.ts,
            "nonce": envelope.nonce,
            "sig": envelope.sig,
        }

    @staticmethod
    def _decode_wire_json(payload: bytes) -> dict[str, object]:
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecureTcpTransportError("invalid wire payload json") from exc
        if not isinstance(parsed, dict):
            raise SecureTcpTransportError("wire payload must be a json object")
        return parsed

    @staticmethod
    def _wire_to_envelope(wire: dict[str, object]) -> SecureEnvelope:
        trace_id = wire.get("trace_id")
        if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
            raise SecureTcpTransportError("trace_id must be null or non-empty string")

        reply_to = wire.get("reply_to")
        if reply_to is not None and (not isinstance(reply_to, str) or not reply_to):
            raise SecureTcpTransportError("reply_to must be null or non-empty string")

        target_raw = wire.get("target")
        if target_raw is None:
            target: str | list[str] | None = None
        elif isinstance(target_raw, str):
            if not target_raw:
                raise SecureTcpTransportError("target must not be empty")
            target = target_raw
        elif isinstance(target_raw, list):
            if not target_raw or not all(isinstance(item, str) and item for item in target_raw):
                raise SecureTcpTransportError("target list entries must be non-empty strings")
            target = list(target_raw)
        else:
            raise SecureTcpTransportError("target must be null, string, or string list")

        payload_b64 = wire.get("payload_b64")
        if not isinstance(payload_b64, str) or not payload_b64:
            raise SecureTcpTransportError("payload_b64 must be a non-empty string")
        try:
            payload_bytes = base64.b64decode(payload_b64.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecureTcpTransportError("payload_b64 is not valid base64") from exc

        headers_raw = wire.get("headers", {})
        if not isinstance(headers_raw, dict):
            raise SecureTcpTransportError("headers must be a mapping")
        headers: dict[str, str] = {}
        for key, value in headers_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SecureTcpTransportError("headers keys/values must be strings")
            headers[key] = value

        kind = wire.get("kind")
        if not isinstance(kind, str) or not kind:
            raise SecureTcpTransportError("kind must be a non-empty string")

        ts = wire.get("ts")
        if not isinstance(ts, int):
            raise SecureTcpTransportError("ts must be an integer")

        nonce = wire.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise SecureTcpTransportError("nonce must be a non-empty string")

        sig = wire.get("sig")
        if not isinstance(sig, str):
            raise SecureTcpTransportError("sig must be a string")

        return SecureEnvelope(
            trace_id=trace_id,
            reply_to=reply_to,
            kind=kind,
            target=target,
            payload_bytes=payload_bytes,
            headers=headers,
            ts=ts,
            nonce=nonce,
            sig=sig,
        )

    def _sign_canonical(self, envelope: SecureEnvelope) -> str:
        body = {
            "trace_id": envelope.trace_id,
            "reply_to": envelope.reply_to,
            "kind": envelope.kind,
            "target": envelope.target,
            "payload_b64": base64.b64encode(envelope.payload_bytes).decode("ascii"),
            "headers": envelope.headers,
            "ts": envelope.ts,
            "nonce": envelope.nonce,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self.config.secret, canonical, digestmod=hashlib.sha256).hexdigest()


def replace_sig(envelope: SecureEnvelope, sig: str) -> SecureEnvelope:
    return SecureEnvelope(
        trace_id=envelope.trace_id,
        reply_to=envelope.reply_to,
        kind=envelope.kind,
        target=envelope.target,
        payload_bytes=envelope.payload_bytes,
        headers=envelope.headers,
        ts=envelope.ts,
        nonce=envelope.nonce,
        sig=sig,
    )
