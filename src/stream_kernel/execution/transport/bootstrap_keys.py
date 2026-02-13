from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass


class RuntimeIpcSecretError(ValueError):
    # Raised when execution IPC secret contract cannot be resolved safely.
    pass


class BootstrapChannelStateError(RuntimeError):
    # Raised when one-shot bootstrap channel is used in an invalid state.
    pass


@dataclass(frozen=True, slots=True)
class ExecutionIpcKeyMaterial:
    # Runtime IPC key material for secure transport signing.
    secret_mode: str
    kdf: str
    master_secret: bytes
    signing_secret: bytes


@dataclass(frozen=True, slots=True)
class BootstrapKeyBundle:
    # One-shot bootstrap payload delivered from supervisor to worker process group.
    created_at_epoch: int
    execution_ipc: ExecutionIpcKeyMaterial


class OneShotBootstrapChannel:
    # One-shot in-memory channel used to transfer bootstrap key bundle exactly once.
    def __init__(self) -> None:
        self._bundle: BootstrapKeyBundle | None = None
        self._consumed = False

    def publish_once(self, bundle: BootstrapKeyBundle) -> None:
        if self._bundle is not None:
            raise BootstrapChannelStateError("bootstrap bundle already published")
        self._bundle = bundle

    def receive_once(self) -> BootstrapKeyBundle:
        if self._bundle is None:
            raise BootstrapChannelStateError("bootstrap bundle is not published")
        if self._consumed:
            raise BootstrapChannelStateError("bootstrap bundle already consumed")
        self._consumed = True
        return self._bundle


def build_bootstrap_key_bundle(
    runtime: dict[str, object],
    *,
    token_bytes_fn: Callable[[int], bytes] | None = None,
    now_fn: Callable[[], int] | None = None,
) -> BootstrapKeyBundle:
    material = resolve_execution_ipc_key_material(runtime, token_bytes_fn=token_bytes_fn)
    timestamp = int((now_fn or (lambda: int(time.time())))())
    return BootstrapKeyBundle(
        created_at_epoch=timestamp,
        execution_ipc=material,
    )


def resolve_execution_ipc_key_material(
    runtime: dict[str, object],
    *,
    token_bytes_fn: Callable[[int], bytes] | None = None,
) -> ExecutionIpcKeyMaterial:
    # Resolve runtime secret contract with deterministic defaults and no secret leakage in diagnostics.
    platform = runtime.get("platform", {})
    if not isinstance(platform, dict):
        raise RuntimeIpcSecretError("runtime.platform must be a mapping")
    execution_ipc = platform.get("execution_ipc", {})
    if not isinstance(execution_ipc, dict):
        raise RuntimeIpcSecretError("runtime.platform.execution_ipc must be a mapping")
    auth = execution_ipc.get("auth", {})
    if not isinstance(auth, dict):
        raise RuntimeIpcSecretError("runtime.platform.execution_ipc.auth must be a mapping")

    secret_mode = auth.get("secret_mode", "static")
    if not isinstance(secret_mode, str) or not secret_mode:
        raise RuntimeIpcSecretError("runtime.platform.execution_ipc.auth.secret_mode must be a non-empty string")

    default_kdf = "hkdf_sha256" if secret_mode == "generated" else "none"
    kdf = auth.get("kdf", default_kdf)
    if not isinstance(kdf, str) or not kdf:
        raise RuntimeIpcSecretError("runtime.platform.execution_ipc.auth.kdf must be a non-empty string")

    if secret_mode == "generated":
        generator = token_bytes_fn or secrets.token_bytes
        master_secret = generator(32)
        if not isinstance(master_secret, bytes) or not master_secret:
            raise RuntimeIpcSecretError("generated execution IPC secret must be non-empty bytes")
    elif secret_mode == "static":
        secret_value = auth.get("secret", "runtime-session-secret")
        if isinstance(secret_value, str):
            master_secret = secret_value.encode("utf-8")
        elif isinstance(secret_value, bytes):
            master_secret = secret_value
        else:
            raise RuntimeIpcSecretError(
                "runtime.platform.execution_ipc.auth.secret must be a string or bytes when provided"
            )
        if not master_secret:
            raise RuntimeIpcSecretError("runtime.platform.execution_ipc.auth.secret must be non-empty")
    else:
        raise RuntimeIpcSecretError(
            "runtime.platform.execution_ipc.auth.secret_mode must be one of: ['generated', 'static']"
        )

    signing_secret = derive_execution_ipc_signing_secret(master_secret=master_secret, kdf=kdf)
    return ExecutionIpcKeyMaterial(
        secret_mode=secret_mode,
        kdf=kdf,
        master_secret=master_secret,
        signing_secret=signing_secret,
    )


def derive_execution_ipc_signing_secret(*, master_secret: bytes, kdf: str) -> bytes:
    if kdf == "none":
        return master_secret
    if kdf == "hkdf_sha256":
        # Step-C baseline: deterministic per-purpose key derivation from one startup master secret.
        return hmac.new(master_secret, b"stream-kernel/execution-ipc/signing", hashlib.sha256).digest()
    raise RuntimeIpcSecretError(
        "runtime.platform.execution_ipc.auth.kdf must be one of: ['hkdf_sha256', 'none']"
    )

