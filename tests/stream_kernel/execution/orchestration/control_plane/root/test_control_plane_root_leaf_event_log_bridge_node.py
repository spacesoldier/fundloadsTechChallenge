from __future__ import annotations

from stream_kernel.execution.orchestration.control_plane.root.system_nodes import (
    ControlPlaneRootLeafEventLogBridgeNode,
)
from stream_kernel.observability.domain.logging import LogMessage
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafDiscoveryAckEvent,
    ControlPlaneLeafDrainReadyEvent,
    ControlPlaneLeafHelloEvent,
    ControlPlaneLeafReplyDispatchDiagEvent,
    ControlPlaneLeafStopAckEvent,
)


def test_root_leaf_event_log_bridge_emits_log_message_for_leaf_hello() -> None:
    node = ControlPlaneRootLeafEventLogBridgeNode()

    produced = node(
        ControlPlaneLeafHelloEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            pid=1001,
            runner_profile="auto",
        ),
        None,
    )

    assert len(produced) == 1
    message = produced[0]
    assert isinstance(message, LogMessage)
    assert message.level == "info"
    assert message.fields.get("event") == "control_plane.root.leaf_connected"
    assert message.fields.get("worker_id") == "execution.ingress#1"


def test_root_leaf_event_log_bridge_emits_warning_for_rejected_discovery_ack() -> None:
    node = ControlPlaneRootLeafEventLogBridgeNode()

    produced = node(
        ControlPlaneLeafDiscoveryAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            request_id="discovery-1",
            status="rejected",
            error="missing node",
        ),
        None,
    )

    assert len(produced) == 1
    message = produced[0]
    assert isinstance(message, LogMessage)
    assert message.level == "warning"
    assert message.fields.get("event") == "control_plane.root.leaf_discovery_ack"
    assert message.fields.get("status") == "rejected"


def test_root_leaf_event_log_bridge_emits_info_for_drain_ready() -> None:
    node = ControlPlaneRootLeafEventLogBridgeNode()

    produced = node(
        ControlPlaneLeafDrainReadyEvent(
            target_group="execution.egress",
            worker_id="execution.egress#1",
            request_id="drain-1",
            tombstone_output=True,
        ),
        None,
    )

    assert len(produced) == 1
    message = produced[0]
    assert isinstance(message, LogMessage)
    assert message.level == "info"
    assert message.fields.get("event") == "control_plane.root.leaf_drain_ready"
    assert message.fields.get("tombstone_output") is True


def test_root_leaf_event_log_bridge_emits_warning_for_non_accepted_stop_ack() -> None:
    node = ControlPlaneRootLeafEventLogBridgeNode()

    produced = node(
        ControlPlaneLeafStopAckEvent(
            target_group="execution.egress",
            worker_id="execution.egress#1",
            command_id="stop-1",
            status="error",
        ),
        None,
    )

    assert len(produced) == 1
    message = produced[0]
    assert isinstance(message, LogMessage)
    assert message.level == "warning"
    assert message.fields.get("event") == "control_plane.root.leaf_stop_ack"
    assert message.fields.get("status") == "error"


def test_root_leaf_event_log_bridge_emits_warning_for_non_applied_config_ack() -> None:
    node = ControlPlaneRootLeafEventLogBridgeNode()

    produced = node(
        ControlPlaneLeafConfigAckEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            config_id="cfg-1",
            status="rejected",
            error="failed apply",
        ),
        None,
    )

    assert len(produced) == 1
    message = produced[0]
    assert isinstance(message, LogMessage)
    assert message.level == "warning"
    assert message.fields.get("event") == "control_plane.root.leaf_config_ack"
    assert message.fields.get("status") == "rejected"


def test_root_leaf_event_log_bridge_emits_reply_dispatch_diag_log() -> None:
    node = ControlPlaneRootLeafEventLogBridgeNode()

    produced = node(
        ControlPlaneLeafReplyDispatchDiagEvent(
            target_group="execution.ingress",
            worker_id="execution.ingress#1",
            request_id="diag-1",
            stage="leaf_reply_dispatch",
            payload_type="ControlPlaneLeafConfigAckEvent",
            status="accepted",
            detail="applied",
        ),
        None,
    )

    assert len(produced) == 1
    message = produced[0]
    assert isinstance(message, LogMessage)
    assert message.level == "info"
    assert message.fields.get("event") == "control_plane.root.leaf_reply_dispatch_diag"
    assert message.fields.get("request_id") == "diag-1"
