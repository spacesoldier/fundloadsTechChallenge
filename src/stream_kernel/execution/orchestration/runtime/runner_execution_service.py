from __future__ import annotations

from dataclasses import dataclass

from stream_kernel.application_context import apply_injection
from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.injection_registry import ScenarioScope
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.runtime.root_loop_orchestration_service import (
    RootRunnerLoopOrchestrationService,
)
from stream_kernel.execution.runtime.runner import AsyncRunner, SyncRunner
from stream_kernel.execution.runtime.runner_ingress import (
    enqueue_runner_input_async,
    enqueue_runner_input_sync,
)
from stream_kernel.routing.envelope import Envelope

DEFAULT_EXECUTION_QUEUE_QUALIFIER = "execution.cpu"
DEFAULT_ASYNC_QUEUE_QUALIFIER = "execution.asyncio"


@service(name="runner_execution_service")
@dataclass(slots=True)
class RunnerExecutionService:
    def run_with_sync_runner(
        self,
        *,
        scenario: object,
        inputs: list[object],
        strict: bool,
        run_id: str,
        scenario_id: str,
        scenario_scope: ScenarioScope,
        full_context_nodes: set[str] | None = None,
        ordered_sink_mode: str = "completion",
        queue_qualifier: str = DEFAULT_EXECUTION_QUEUE_QUALIFIER,
    ) -> None:
        loop_orchestration = RootRunnerLoopOrchestrationService()
        nodes = {spec.name: spec.step for spec in scenario.steps}
        runner = SyncRunner(
            nodes=nodes,
            run_id=run_id,
            scenario_id=scenario_id,
            full_context_nodes=set(full_context_nodes or ()),
            ordered_sink_mode=ordered_sink_mode,
        )
        if queue_qualifier != DEFAULT_EXECUTION_QUEUE_QUALIFIER:
            runner.work_queue = inject.queue(Envelope, qualifier=queue_qualifier)
        apply_injection(runner, scenario_scope, strict)
        try:
            loop_orchestration.execute_sync(
                runner=runner,
                inputs=inputs,
                run_id=run_id,
                scenario_id=scenario_id,
                scenario_scope=scenario_scope,
                enqueue_runner_input=enqueue_runner_input_sync,
            )
        finally:
            runner.on_run_end()
            close_scenario_scope(scenario_scope)

    def run_with_async_runner(
        self,
        *,
        scenario: object,
        inputs: list[object],
        strict: bool,
        run_id: str,
        scenario_id: str,
        scenario_scope: ScenarioScope,
        full_context_nodes: set[str] | None = None,
        ordered_sink_mode: str = "completion",
        queue_qualifier: str = DEFAULT_ASYNC_QUEUE_QUALIFIER,
    ) -> None:
        loop_orchestration = RootRunnerLoopOrchestrationService()
        nodes = {spec.name: spec.step for spec in scenario.steps}
        runner = AsyncRunner(
            nodes=nodes,
            run_id=run_id,
            scenario_id=scenario_id,
            full_context_nodes=set(full_context_nodes or ()),
            ordered_sink_mode=ordered_sink_mode,
        )
        if queue_qualifier != DEFAULT_ASYNC_QUEUE_QUALIFIER:
            runner.work_queue = inject.queue(Envelope, qualifier=queue_qualifier)
        apply_injection(runner, scenario_scope, strict)
        try:
            loop_orchestration.execute_async(
                runner=runner,
                inputs=inputs,
                run_id=run_id,
                scenario_id=scenario_id,
                scenario_scope=scenario_scope,
                enqueue_runner_input=enqueue_runner_input_async,
            )
        finally:
            runner.on_run_end()
            close_scenario_scope(scenario_scope)


def run_with_sync_runner(
    *,
    scenario: object,
    inputs: list[object],
    strict: bool,
    run_id: str,
    scenario_id: str,
    scenario_scope: ScenarioScope,
    full_context_nodes: set[str] | None = None,
    ordered_sink_mode: str = "completion",
    queue_qualifier: str = DEFAULT_EXECUTION_QUEUE_QUALIFIER,
) -> None:
    RunnerExecutionService().run_with_sync_runner(
        scenario=scenario,
        inputs=inputs,
        strict=strict,
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_scope=scenario_scope,
        full_context_nodes=full_context_nodes,
        ordered_sink_mode=ordered_sink_mode,
        queue_qualifier=queue_qualifier,
    )


def run_with_async_runner(
    *,
    scenario: object,
    inputs: list[object],
    strict: bool,
    run_id: str,
    scenario_id: str,
    scenario_scope: ScenarioScope,
    full_context_nodes: set[str] | None = None,
    ordered_sink_mode: str = "completion",
    queue_qualifier: str = DEFAULT_ASYNC_QUEUE_QUALIFIER,
) -> None:
    RunnerExecutionService().run_with_async_runner(
        scenario=scenario,
        inputs=inputs,
        strict=strict,
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_scope=scenario_scope,
        full_context_nodes=full_context_nodes,
        ordered_sink_mode=ordered_sink_mode,
        queue_qualifier=queue_qualifier,
    )


def close_scenario_scope(scope: ScenarioScope) -> None:
    close = getattr(scope, "close", None)
    if callable(close):
        close()
