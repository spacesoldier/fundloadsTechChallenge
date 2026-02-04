from __future__ import annotations

import pytest

# Stage metadata rules live in docs/framework/initial_stage/Node and stage specifications.md.
from stream_kernel.kernel.stage import stage


def test_stage_decorator_rejects_empty_name() -> None:
    # Stage names must be non-empty for deterministic grouping.
    with pytest.raises(ValueError):
        stage(name="")(object)
