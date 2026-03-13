from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.execution.orchestration.control_plane.initialization_nodes import (
    ControlPlaneInitializationDispatchNode,
    ControlPlaneInitializationPlanNode,
    ControlPlaneNodeInitializeNode,
    ControlPlaneReadyForWorkNode,
)
from stream_kernel.integration.kv_store import InMemoryKvStore
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneInitEvent,
    ControlPlaneInitializationCompletedEvent,
    ControlPlaneInitializationRequestedEvent,
    ControlPlaneNodeInitializeCommand,
    ControlPlaneReadyForWorkEvent,
)
from stream_kernel.platform.services.runtime.control_plane_node_initialization import (
    InMemoryControlPlaneNodeInitializationService,
)


@dataclass
class _NodeWithInitialize:
    initialized: bool = False

    def initialize(self) -> None:
        self.initialized = True


def test_initialization_dispatch_node_emits_requested_event() -> None:
    node = ControlPlaneInitializationDispatchNode()

    produced = node(ControlPlaneInitEvent(runtime={"k": "v"}), None)

    assert len(produced) == 1
    assert isinstance(produced[0], ControlPlaneInitializationRequestedEvent)
    assert produced[0].runtime == {"k": "v"}


def test_initialization_plan_and_initialize_node_complete_phase() -> None:
    service = InMemoryControlPlaneNodeInitializationService(store=InMemoryKvStore())
    plan = ControlPlaneInitializationPlanNode(
        initialization=service,
        candidate_node_names=("source:a",),
    )
    runtime = {"platform": {}}

    commands = plan(ControlPlaneInitializationRequestedEvent(runtime=runtime), None)

    assert len(commands) == 1
    assert isinstance(commands[0], ControlPlaneNodeInitializeCommand)
    assert commands[0].node_name == "source:a"

    lookup = {"source:a": _NodeWithInitialize()}
    initialize = ControlPlaneNodeInitializeNode(initialization=service, node_lookup=lookup)
    produced = initialize(commands[0], None)
    if hasattr(produced, "__await__"):
        import asyncio

        produced = asyncio.run(produced)  # type: ignore[assignment]

    assert lookup["source:a"].initialized is True
    assert any(isinstance(item, ControlPlaneInitializationCompletedEvent) for item in produced)


def test_ready_for_work_node_emits_ready_event() -> None:
    node = ControlPlaneReadyForWorkNode()

    produced = node(
        ControlPlaneInitializationCompletedEvent(
            runtime={"run": "x"},
            initialized_nodes=("n1",),
            expected_nodes=("n1",),
        ),
        None,
    )

    assert produced == [ControlPlaneReadyForWorkEvent(runtime={"run": "x"})]
