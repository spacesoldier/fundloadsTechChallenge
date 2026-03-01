from __future__ import annotations

from stream_kernel.execution.orchestration.lifecycle.leaf.startup.bootstrap_models import ChildBootstrapBundle
from stream_kernel.execution.orchestration.control_plane.bootstrap_keys import (
    BootstrapKeyBundle,
    ExecutionIpcKeyMaterial,
)
from stream_kernel.execution.orchestration.lifecycle.leaf.startup.child_bundle import (
    project_child_bundle_for_group,
    resolve_child_process_role,
)


def _key_bundle() -> BootstrapKeyBundle:
    return BootstrapKeyBundle(
        created_at_epoch=1,
        execution_ipc=ExecutionIpcKeyMaterial(
            secret_mode="explicit",
            kdf="none",
            master_secret=b"m",
            signing_secret=b"s",
        ),
    )


def test_project_child_bundle_for_group_sets_worker_role_by_default() -> None:
    bundle = ChildBootstrapBundle(
        scenario_id="scenario",
        process_group=None,
        discovery_modules=["m"],
        runtime={},
        key_bundle=_key_bundle(),
        run_id="run",
        adapters={"a": 1},
        config={"c": 2},
    )

    projected = project_child_bundle_for_group(bundle, "execution.alpha")

    assert isinstance(projected, ChildBootstrapBundle)
    assert projected is not bundle
    assert projected.process_group == "execution.alpha"
    assert projected.runtime["__process_role"] == "worker"
    assert bundle.runtime.get("__process_role") is None


def test_project_child_bundle_for_group_marks_observability_owner_role() -> None:
    bundle = ChildBootstrapBundle(
        scenario_id="scenario",
        process_group=None,
        discovery_modules=["m"],
        runtime={
            "observability": {
                "service_process": {
                    "enabled": True,
                    "group_name": "system.observability",
                }
            }
        },
        key_bundle=_key_bundle(),
    )

    projected = project_child_bundle_for_group(bundle, "system.observability")

    assert projected.runtime["__process_role"] == "observability_worker"


def test_project_child_bundle_for_group_marks_observability_owner_role_from_legacy_service_worker() -> None:
    bundle = ChildBootstrapBundle(
        scenario_id="scenario",
        process_group=None,
        discovery_modules=["m"],
        runtime={
            "observability": {
                "service_worker": {
                    "enabled": True,
                    "group_name": "system.observability",
                }
            }
        },
        key_bundle=_key_bundle(),
    )

    projected = project_child_bundle_for_group(bundle, "system.observability")

    assert projected.runtime["__process_role"] == "observability_worker"


def test_resolve_child_process_role_returns_worker_for_non_owner_group() -> None:
    role = resolve_child_process_role(
        runtime={
            "observability": {
                "service_process": {
                    "enabled": True,
                    "group_name": "system.observability",
                }
            }
        },
        group_name="execution.alpha",
    )

    assert role == "worker"


def test_project_child_bundle_for_group_propagates_runner_profile() -> None:
    bundle = ChildBootstrapBundle(
        scenario_id="scenario",
        process_group=None,
        discovery_modules=["m"],
        runtime={},
        key_bundle=_key_bundle(),
    )

    projected = project_child_bundle_for_group(
        bundle,
        "execution.alpha",
        runner_profile="sync",
    )

    assert projected.runtime["__runner_profile_requested"] == "sync"
