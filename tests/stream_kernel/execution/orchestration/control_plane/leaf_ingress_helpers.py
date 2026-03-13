from __future__ import annotations

from stream_kernel.execution.transport.ipc.ipc_transport import (
    EXECUTION_IPC_LANE_CONTROL,
    EXECUTION_IPC_LANE_DATA,
    EXECUTION_IPC_LANE_LOG,
    EXECUTION_IPC_LANE_METRIC,
    EXECUTION_IPC_LANE_TRACE,
)


def drain_worker_replies(
    ingress: object,
    *,
    worker_id: str,
    timeout_seconds: float = 0.0,
    max_items: int = 64,
) -> int:
    if not isinstance(worker_id, str) or not worker_id:
        return 0
    poll = getattr(ingress, "poll_next_leaf_ingress_for_worker_lane", None)
    dispatch = getattr(ingress, "dispatch_polled_leaf_ingress", None)
    if not callable(poll) or not callable(dispatch):
        return 0
    lanes = (
        EXECUTION_IPC_LANE_CONTROL,
        EXECUTION_IPC_LANE_DATA,
        EXECUTION_IPC_LANE_TRACE,
        EXECUTION_IPC_LANE_LOG,
        EXECUTION_IPC_LANE_METRIC,
    )
    drained = 0
    limit = max(1, int(max_items))
    for item_index in range(limit):
        first_timeout = max(0.0, float(timeout_seconds)) if item_index == 0 else 0.0
        selected_lane: str | None = None
        selected_payload: object | None = None
        for lane_index, lane in enumerate(lanes):
            lane_timeout = first_timeout if lane_index == 0 else 0.0
            payload = poll(
                worker_id=worker_id,
                lane=lane,
                timeout_seconds=lane_timeout,
            )
            if payload is None:
                continue
            selected_lane = lane
            selected_payload = payload
            break
        if selected_payload is None:
            break
        try:
            handled = bool(
                dispatch(
                    worker_id=worker_id,
                    payload=selected_payload,
                    lane=selected_lane,
                )
            )
        except TypeError:
            handled = bool(dispatch(worker_id=worker_id, payload=selected_payload))
        if handled:
            drained += 1
    return drained
