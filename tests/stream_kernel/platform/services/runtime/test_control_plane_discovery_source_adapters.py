from __future__ import annotations

from types import ModuleType

from stream_kernel.adapters.contracts import adapter
from stream_kernel.application_context.service import service
from stream_kernel.kernel.node_annotation import node
from stream_kernel.platform.services.runtime.control_plane_discovery_adapters import (
    ScopedControlPlaneDiscoverySourceAdapter,
)


class _TestScopedAdapter(ScopedControlPlaneDiscoverySourceAdapter):
    def __init__(
        self,
        *,
        source_scope: str,
        roots: tuple[str, ...],
        modules: dict[str, ModuleType],
        edges: dict[str, tuple[str, ...]],
    ) -> None:
        super().__init__(source_scope=source_scope, roots=roots)
        self._modules = modules
        self._edges = edges

    def _import_module(self, module_name: str) -> ModuleType:
        return self._modules[module_name]

    def _iter_submodule_names(self, module_name: str, module: ModuleType) -> tuple[str, ...]:
        _ = module
        return tuple(self._edges.get(module_name, tuple()))


def _module(name: str, *, is_package: bool = False) -> ModuleType:
    module = ModuleType(name)
    if is_package:
        module.__dict__["__path__"] = [f"/<memory>/{name.replace('.', '/')}"]
    return module


def test_scoped_discovery_adapter_emits_only_its_scope_records() -> None:
    platform_root = _module("platform.root", is_package=True)
    project_root = _module("project.root", is_package=True)

    @node(name="platform.node")
    def platform_node(payload: object) -> object:
        return payload

    @service(name="project.service")
    class ProjectService:
        pass

    platform_root.platform_node = platform_node
    project_root.ProjectService = ProjectService

    modules = {
        "platform.root": platform_root,
        "project.root": project_root,
    }
    edges: dict[str, tuple[str, ...]] = {}

    platform_adapter = _TestScopedAdapter(
        source_scope="platform",
        roots=("platform.root",),
        modules=modules,
        edges=edges,
    )
    project_adapter = _TestScopedAdapter(
        source_scope="project",
        roots=("project.root",),
        modules=modules,
        edges=edges,
    )

    platform_batch, platform_next = platform_adapter.next_batch(runtime={}, cursor=0, limit=128)
    project_batch, project_next = project_adapter.next_batch(runtime={}, cursor=0, limit=128)

    assert platform_next is None
    assert project_next is None
    assert platform_batch
    assert project_batch
    assert all(item.source_scope == "platform" for item in platform_batch)
    assert all(item.source_scope == "project" for item in project_batch)


def test_scoped_discovery_adapter_traversal_is_iterative_and_batch_limited() -> None:
    depth = 1200
    modules: dict[str, ModuleType] = {}
    edges: dict[str, tuple[str, ...]] = {}

    root_name = "deep.root"
    root = _module(root_name, is_package=True)
    modules[root_name] = root
    previous = root_name

    for idx in range(depth):
        current = f"deep.root.m{idx}"
        mod = _module(current, is_package=True)

        @node(name=f"deep.node.{idx}")
        def deep_node(payload: object) -> object:
            return payload

        mod.__dict__[f"deep_node_{idx}"] = deep_node
        modules[current] = mod
        edges[previous] = (current,)
        previous = current

    adapter = _TestScopedAdapter(
        source_scope="platform",
        roots=(root_name,),
        modules=modules,
        edges=edges,
    )

    first_batch, first_next = adapter.next_batch(runtime={}, cursor=0, limit=64)

    assert len(first_batch) == 64
    assert first_next == 64


def test_scoped_discovery_adapter_output_order_is_stable_for_same_module_tree() -> None:
    root = _module("tree.root", is_package=True)
    mod_a = _module("tree.root.a")
    mod_b = _module("tree.root.b")

    @node(name="tree.node.b")
    def node_b(payload: object) -> object:
        return payload

    @node(name="tree.node.a")
    def node_a(payload: object) -> object:
        return payload

    @service(name="tree.service")
    class TreeService:
        pass

    @adapter(name="tree.adapter", kind="tree.kind", consumes=[], emits=[])
    def tree_adapter_factory(settings: dict[str, object]) -> object:
        return settings

    mod_a.node_a = node_a
    mod_b.node_b = node_b
    root.TreeService = TreeService
    root.tree_adapter_factory = tree_adapter_factory

    modules = {
        "tree.root": root,
        "tree.root.a": mod_a,
        "tree.root.b": mod_b,
    }
    edges = {"tree.root": ("tree.root.b", "tree.root.a")}

    adapter_under_test = _TestScopedAdapter(
        source_scope="project",
        roots=("tree.root",),
        modules=modules,
        edges=edges,
    )

    first, _ = adapter_under_test.next_batch(runtime={}, cursor=0, limit=256)
    second, _ = adapter_under_test.next_batch(runtime={}, cursor=0, limit=256)

    first_keys = [(item.module, item.qualname) for item in first]
    second_keys = [(item.module, item.qualname) for item in second]
    assert first_keys == second_keys
    assert first_keys == sorted(first_keys)
