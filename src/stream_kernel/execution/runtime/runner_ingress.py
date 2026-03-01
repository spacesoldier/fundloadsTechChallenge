from __future__ import annotations

from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.routing.envelope import Envelope


def enqueue_runner_input_sync(
    runner: SyncRunner,
    payload: object,
    *,
    run_id: str,
    scenario_id: str,
    index: int,
) -> None:
    context_service = runner._context_service()
    work_queue = runner._work_queue()
    router = runner._router()
    if isinstance(payload, Envelope):
        trace_id = payload.trace_id or runner._trace_id(run_id=run_id, index=index)
        runner._seed_context(
            context_service=context_service,
            trace_id=trace_id,
            payload=payload.payload,
            run_id=run_id,
            scenario_id=scenario_id,
            reply_to=payload.reply_to,
        )
        ingress_service_outputs = runner._emit_ingress(trace_id=trace_id, reply_to=payload.reply_to)
        runner._route_observability_service_outputs(
            service_outputs=ingress_service_outputs,
            source_node="__ingress__",
            trace_id=trace_id,
            reply_to=payload.reply_to,
            span_id=payload.span_id,
            work_queue=work_queue,
            router=router,
        )
        if payload.target is not None:
            work_queue.push(
                Envelope(
                    payload=payload.payload,
                    target=payload.target,
                    trace_id=trace_id,
                    reply_to=payload.reply_to,
                    span_id=payload.span_id,
                )
            )
            return
        routing_result = router.route([payload.payload])
        for target_name, routed_payload in runner._local_deliveries(routing_result):
            work_queue.push(
                Envelope(
                    payload=routed_payload,
                    target=target_name,
                    trace_id=trace_id,
                    reply_to=payload.reply_to,
                    span_id=payload.span_id,
                )
            )
        return

    trace_id = runner._trace_id(run_id=run_id, index=index)
    runner._seed_context(
        context_service=context_service,
        trace_id=trace_id,
        payload=payload,
        run_id=run_id,
        scenario_id=scenario_id,
    )
    ingress_service_outputs = runner._emit_ingress(trace_id=trace_id, reply_to=None)
    runner._route_observability_service_outputs(
        service_outputs=ingress_service_outputs,
        source_node="__ingress__",
        trace_id=trace_id,
        reply_to=None,
        span_id=None,
        work_queue=work_queue,
        router=router,
    )
    routing_result = router.route([payload])
    for target_name, routed_payload in runner._local_deliveries(routing_result):
        work_queue.push(Envelope(payload=routed_payload, target=target_name, trace_id=trace_id))


def enqueue_runner_input_async(
    runner: AsyncRunner,
    payload: object,
    *,
    run_id: str,
    scenario_id: str,
    index: int,
) -> None:
    context_service = runner._context_service()
    work_queue = runner._work_queue()
    router = runner._router()
    if isinstance(payload, Envelope):
        trace_id = payload.trace_id or SyncRunner._trace_id(run_id=run_id, index=index)
        SyncRunner._seed_context(
            context_service=context_service,
            trace_id=trace_id,
            payload=payload.payload,
            run_id=run_id,
            scenario_id=scenario_id,
            reply_to=payload.reply_to,
        )
        ingress_service_outputs = runner._emit_ingress(trace_id=trace_id, reply_to=payload.reply_to)
        runner._route_observability_service_outputs(
            service_outputs=ingress_service_outputs,
            source_node="__ingress__",
            trace_id=trace_id,
            reply_to=payload.reply_to,
            span_id=payload.span_id,
            work_queue=work_queue,
            router=router,
        )
        if payload.target is not None:
            work_queue.push(
                Envelope(
                    payload=payload.payload,
                    target=payload.target,
                    trace_id=trace_id,
                    reply_to=payload.reply_to,
                    span_id=payload.span_id,
                )
            )
            return
        routing_result = router.route([payload.payload])
        for target_name, routed_payload in SyncRunner._local_deliveries(routing_result):
            work_queue.push(
                Envelope(
                    payload=routed_payload,
                    target=target_name,
                    trace_id=trace_id,
                    reply_to=payload.reply_to,
                    span_id=payload.span_id,
                )
            )
        return

    trace_id = SyncRunner._trace_id(run_id=run_id, index=index)
    SyncRunner._seed_context(
        context_service=context_service,
        trace_id=trace_id,
        payload=payload,
        run_id=run_id,
        scenario_id=scenario_id,
    )
    ingress_service_outputs = runner._emit_ingress(trace_id=trace_id, reply_to=None)
    runner._route_observability_service_outputs(
        service_outputs=ingress_service_outputs,
        source_node="__ingress__",
        trace_id=trace_id,
        reply_to=None,
        span_id=None,
        work_queue=work_queue,
        router=router,
    )
    routing_result = router.route([payload])
    for target_name, routed_payload in SyncRunner._local_deliveries(routing_result):
        work_queue.push(Envelope(payload=routed_payload, target=target_name, trace_id=trace_id))


__all__ = [
    "enqueue_runner_input_sync",
    "enqueue_runner_input_async",
]

