from __future__ import annotations

import pytest

from stream_kernel.execution.transport.bootstrap_keys import (
    BootstrapChannelStateError,
    OneShotBootstrapChannel,
    RuntimeIpcSecretError,
    build_bootstrap_key_bundle,
    resolve_execution_ipc_key_material,
)


def test_resolve_execution_ipc_key_material_generated_mode_uses_non_empty_bytes() -> None:
    # KEY-IPC-01: generated mode must produce non-empty random-like bytes.
    runtime = {
        "platform": {
            "execution_ipc": {
                "auth": {
                    "secret_mode": "generated",
                    "kdf": "hkdf_sha256",
                }
            }
        }
    }

    material = resolve_execution_ipc_key_material(runtime, token_bytes_fn=lambda n: b"x" * n)
    assert material.secret_mode == "generated"
    assert material.kdf == "hkdf_sha256"
    assert material.master_secret == b"x" * 32
    assert isinstance(material.signing_secret, bytes)
    assert material.signing_secret


def test_resolve_execution_ipc_key_material_static_mode_keeps_explicit_secret() -> None:
    # KEY-IPC-02: static secret mode remains supported.
    runtime = {
        "platform": {
            "execution_ipc": {
                "auth": {
                    "secret_mode": "static",
                    "kdf": "none",
                    "secret": "phase3-static-secret",
                }
            }
        }
    }

    material = resolve_execution_ipc_key_material(runtime)
    assert material.secret_mode == "static"
    assert material.kdf == "none"
    assert material.master_secret == b"phase3-static-secret"
    assert material.signing_secret == b"phase3-static-secret"


def test_bootstrap_channel_delivers_bundle_once() -> None:
    # KEY-IPC-03: bootstrap bundle channel must be one-shot.
    runtime = {
        "platform": {
            "execution_ipc": {
                "auth": {
                    "secret_mode": "generated",
                    "kdf": "hkdf_sha256",
                }
            }
        }
    }
    bundle = build_bootstrap_key_bundle(
        runtime,
        token_bytes_fn=lambda n: b"z" * n,
        now_fn=lambda: 123,
    )

    channel = OneShotBootstrapChannel()
    channel.publish_once(bundle)
    received = channel.receive_once()
    assert received.created_at_epoch == 123
    assert received.execution_ipc.master_secret == b"z" * 32

    with pytest.raises(BootstrapChannelStateError, match="already consumed"):
        channel.receive_once()


def test_bootstrap_channel_rejects_second_publish() -> None:
    # KEY-IPC-03b: second publish must fail deterministically.
    runtime = {
        "platform": {
            "execution_ipc": {
                "auth": {
                    "secret_mode": "generated",
                    "kdf": "hkdf_sha256",
                }
            }
        }
    }
    bundle = build_bootstrap_key_bundle(runtime, token_bytes_fn=lambda n: b"k" * n, now_fn=lambda: 1)
    channel = OneShotBootstrapChannel()
    channel.publish_once(bundle)
    with pytest.raises(BootstrapChannelStateError, match="already published"):
        channel.publish_once(bundle)


def test_resolve_execution_ipc_key_material_redacts_secret_value_in_errors() -> None:
    # KEY-IPC-04: secret values must not leak into raised diagnostics.
    class _SecretObject:
        def __repr__(self) -> str:  # pragma: no cover - representation only
            return "LEAK_THIS_SECRET"

        __str__ = __repr__

    runtime = {
        "platform": {
            "execution_ipc": {
                "auth": {
                    "secret_mode": "static",
                    "kdf": "none",
                    "secret": _SecretObject(),
                }
            }
        }
    }

    with pytest.raises(RuntimeIpcSecretError) as excinfo:
        resolve_execution_ipc_key_material(runtime)
    assert "LEAK_THIS_SECRET" not in str(excinfo.value)

