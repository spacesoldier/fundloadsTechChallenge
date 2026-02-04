from __future__ import annotations

import types

from stream_kernel.kernel.node import node
from stream_kernel.kernel.stage import stage
from stream_kernel.kernel.discovery import discover_nodes


def test_stage_container_method_nodes_are_discovered() -> None:
    mod = types.ModuleType("stage_method_nodes")

    @stage(name="parse")
    class ParseStage:
        @node(name="a")
        def do_a(self, msg: object, ctx: object | None) -> list[object]:
            return [msg]

    mod.ParseStage = ParseStage

    nodes = discover_nodes([mod])
    assert len(nodes) == 1
    node_def = nodes[0]
    assert node_def.meta.name == "a"
    assert node_def.meta.stage == "parse"
    assert node_def.container_cls is ParseStage
    assert node_def.container_attr == "do_a"
