from __future__ import annotations

# Step-to-step data contracts are defined in docs/implementation/steps/*
# and expressed via @node consumes/emits metadata for DAG construction.

from fund_load.domain.messages import Decision as DomainDecision
from fund_load.domain.messages import LoadAttempt, RawLine
from fund_load.usecases.messages import (
    AttemptWithKeys,
    Decision as UsecaseDecision,
    EnrichedAttempt,
    IdempotencyClassifiedAttempt,
    OutputLine,
    WindowedDecision,
)
from fund_load.usecases.steps.compute_features import ComputeFeatures
from fund_load.usecases.steps.compute_time_keys import ComputeTimeKeys
from fund_load.usecases.steps.evaluate_policies import EvaluatePolicies
from fund_load.usecases.steps.format_output import FormatOutput
from fund_load.usecases.steps.idempotency_gate import IdempotencyGate
from fund_load.usecases.steps.io_bridge import EgressLineBridge, IngressLineBridge
from fund_load.usecases.steps.parse_load_attempt import ParseLoadAttempt
from fund_load.usecases.steps.update_windows import UpdateWindows
from stream_kernel.kernel.node import NodeMeta
from stream_kernel.adapters.file_io import SinkLine, TextRecord


def _meta(obj: object) -> NodeMeta:
    meta = getattr(obj, "__node_meta__", None)
    assert isinstance(meta, NodeMeta)
    return meta


def test_parse_load_attempt_node_contract() -> None:
    # Step 01 consumes RawLine and emits LoadAttempt or a Decision (docs/implementation/steps/01 ParseLoadAttempt.md).
    meta = _meta(ParseLoadAttempt)
    assert meta.consumes == [RawLine]
    assert meta.emits == [LoadAttempt, DomainDecision]


def test_ingress_line_bridge_node_contract() -> None:
    # Graph-native file source emits text transport records for text formats; bridge converts to domain RawLine.
    meta = _meta(IngressLineBridge)
    assert meta.consumes == [TextRecord]
    assert meta.emits == [RawLine]


def test_compute_time_keys_node_contract() -> None:
    # Step 02 consumes LoadAttempt and emits AttemptWithKeys (docs/implementation/steps/02 ComputeTimeKeys.md).
    meta = _meta(ComputeTimeKeys)
    assert meta.consumes == [LoadAttempt]
    assert meta.emits == [AttemptWithKeys]


def test_idempotency_gate_node_contract() -> None:
    # Step 03 consumes AttemptWithKeys and emits IdempotencyClassifiedAttempt (docs/implementation/steps/03 IdempotencyGate.md).
    meta = _meta(IdempotencyGate)
    assert meta.consumes == [AttemptWithKeys]
    assert meta.emits == [IdempotencyClassifiedAttempt]


def test_compute_features_node_contract() -> None:
    # Step 04 consumes IdempotencyClassifiedAttempt and emits EnrichedAttempt (docs/implementation/steps/04 ComputeFeatures.md).
    meta = _meta(ComputeFeatures)
    assert meta.consumes == [IdempotencyClassifiedAttempt]
    assert meta.emits == [EnrichedAttempt]


def test_evaluate_policies_node_contract() -> None:
    # Step 05 consumes EnrichedAttempt and emits Decision (docs/implementation/steps/05 EvaluatePolicies.md).
    meta = _meta(EvaluatePolicies)
    assert meta.consumes == [EnrichedAttempt]
    assert meta.emits == [UsecaseDecision]


def test_update_windows_node_contract() -> None:
    # Step 06 consumes Decision and emits WindowedDecision (stage-specific token split).
    meta = _meta(UpdateWindows)
    assert meta.consumes == [UsecaseDecision]
    assert meta.emits == [WindowedDecision]


def test_format_output_node_contract() -> None:
    # Step 07 consumes WindowedDecision and emits OutputLine (docs/implementation/steps/07 FormatOutput.md).
    meta = _meta(FormatOutput)
    assert meta.consumes == [WindowedDecision]
    assert meta.emits == [OutputLine]


def test_egress_line_bridge_node_contract() -> None:
    # Graph-native file sink consumes SinkLine; bridge converts from OutputLine.
    meta = _meta(EgressLineBridge)
    assert meta.consumes == [OutputLine]
    assert meta.emits == [SinkLine]
