from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from stream_kernel.application_context.inject import inject
from stream_kernel.application_context.service import service
from stream_kernel.execution.orchestration.lifecycle.leaf.debug_logging import leaf_debug_log
from stream_kernel.platform.services.runtime.control_plane_bootstrapper import (
    ControlPlaneBootstrapperService,
)
from stream_kernel.platform.services.runtime.control_plane_config_stream import (
    ControlPlaneStartupConfigStore,
)
from stream_kernel.platform.services.runtime.control_plane_discovery import (
    ControlPlaneDiscoveryService,
)
from stream_kernel.platform.services.runtime.control_plane_events import (
    ControlPlaneDiscoveryItemEvent,
    ControlPlaneDiscoveryEntityRecord,
    ControlPlaneLeafConfigAckEvent,
    ControlPlaneLeafConfigCardEvent,
)

if TYPE_CHECKING:
    from stream_kernel.execution.orchestration.lifecycle.leaf.runtime.worker_runtime import (
        LeafWorkerRuntimeSession,
    )


@runtime_checkable
class LeafRuntimeActivationService(Protocol):
    def apply_config(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        card: ControlPlaneLeafConfigCardEvent,
    ) -> ControlPlaneLeafConfigAckEvent:
        raise NotImplementedError


@service(name="leaf_runtime_activation_service")
@dataclass(slots=True)
class DefaultLeafRuntimeActivationService(LeafRuntimeActivationService):
    discovery: ControlPlaneDiscoveryService = inject.service(ControlPlaneDiscoveryService)
    config_store: ControlPlaneStartupConfigStore = inject.service(ControlPlaneStartupConfigStore)
    bootstrapper: ControlPlaneBootstrapperService = inject.service(ControlPlaneBootstrapperService)
    allow_local_discovery_fallback: bool = False

    def apply_config(
        self,
        *,
        session: "LeafWorkerRuntimeSession",
        card: ControlPlaneLeafConfigCardEvent,
    ) -> ControlPlaneLeafConfigAckEvent:
        leaf_debug_log(
            event="leaf.runtime_activation.apply_config.started",
            worker_id=session.worker_id,
            config_id=card.config_id,
            node_count=len(card.nodes),
        )
        try:
            self._ensure_discovery_populated(session=session)
            discovered = self._discovered_node_names()
            runtime_nodes = self._runtime_node_names(session)
            known_nodes = discovered | runtime_nodes
            known_aliases = self._expand_known_aliases(known_nodes)
            missing_nodes = [
                name
                for name in card.nodes
                if name not in known_aliases and not self._is_transport_alias(name)
            ]
            if missing_nodes and any(not self._is_transport_alias(name) for name in missing_nodes):
                missing = ", ".join(sorted(set(missing_nodes)))
                return ControlPlaneLeafConfigAckEvent(
                    target_group=session.group_name,
                    worker_id=session.worker_id,
                    config_id=card.config_id,
                    status="rejected",
                    error=f"missing runtime metadata for nodes: {missing}",
                )
            # Node-level config records are optional; do not block startup on absent
            # config entries. This contract only validates runtime/discovery presence.
            _ = self._configured_node_names()
            resolved_nodes = tuple(
                name
                for name in card.nodes
                if name in known_aliases or self._is_transport_alias(name)
            )
            ack = ControlPlaneLeafConfigAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                config_id=card.config_id,
                status="applied",
                resolved_nodes=resolved_nodes or tuple(card.nodes),
            )
            leaf_debug_log(
                event="leaf.runtime_activation.apply_config.completed",
                worker_id=session.worker_id,
                config_id=card.config_id,
                status=ack.status,
                resolved_count=len(ack.resolved_nodes),
            )
            return ack
        except Exception as exc:  # noqa: BLE001 - deterministic typed ack
            leaf_debug_log(
                event="leaf.runtime_activation.apply_config.failed",
                worker_id=session.worker_id,
                config_id=card.config_id,
                error=exc.__class__.__name__,
            )
            return ControlPlaneLeafConfigAckEvent(
                target_group=session.group_name,
                worker_id=session.worker_id,
                config_id=card.config_id,
                status="rejected",
                error=str(exc) or exc.__class__.__name__,
            )

    def _ensure_discovery_populated(self, *, session: "LeafWorkerRuntimeSession") -> None:
        items_reader = getattr(self.discovery, "items", None)
        if callable(items_reader):
            try:
                existing = items_reader()
                if isinstance(existing, list) and existing:
                    return
            except Exception:
                pass
        if not self._local_discovery_fallback_enabled(session):
            return
        bootstrapper = self.bootstrapper
        discover_all = getattr(bootstrapper, "discover_all", None)
        if not callable(discover_all):
            return
        runtime = self._runtime_from_session(session)
        discovered = list(discover_all(runtime=runtime))
        if not discovered:
            return
        appender = getattr(self.discovery, "append_item", None)
        if not callable(appender):
            return
        for item in discovered:
            appender(item)

    def _discovered_node_names(self) -> set[str]:
        records = list(self.discovery.entity_records(kind="node"))
        names: set[str] = set()
        for item in records:
            if not isinstance(item, ControlPlaneDiscoveryEntityRecord):
                continue
            node_name = item.meta.get("name")
            if isinstance(node_name, str) and node_name:
                names.add(node_name)
        items_reader = getattr(self.discovery, "items", None)
        if callable(items_reader):
            try:
                items = items_reader()
            except Exception:
                items = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, ControlPlaneDiscoveryItemEvent):
                        continue
                    if item.item_kind != "node":
                        continue
                    node_name = item.payload.get("name")
                    if isinstance(node_name, str) and node_name:
                        names.add(node_name)
        return names

    def _configured_node_names(self) -> set[str]:
        records = list(self.config_store.records(section="node"))
        names: set[str] = set()
        for record in records:
            payload = getattr(record, "payload", None)
            if isinstance(payload, dict):
                node_name = payload.get("name")
                if isinstance(node_name, str) and node_name:
                    names.add(node_name)
                    continue
            record_id = getattr(record, "record_id", "")
            if isinstance(record_id, str) and record_id.startswith("node:") and len(record_id) > len("node:"):
                names.add(record_id[len("node:") :])
        return names

    @staticmethod
    def _runtime_node_names(session: "LeafWorkerRuntimeSession") -> set[str]:
        child = getattr(session, "child", None)
        scenario_steps = getattr(child, "scenario_steps", None)
        if not isinstance(scenario_steps, dict):
            return set()
        names: set[str] = set()
        for node_name in scenario_steps.keys():
            if isinstance(node_name, str) and node_name:
                names.add(node_name)
        return names

    @staticmethod
    def _runtime_from_session(session: "LeafWorkerRuntimeSession") -> dict[str, object]:
        child = getattr(session, "child", None)
        runtime = getattr(child, "runtime", None)
        if isinstance(runtime, dict):
            return dict(runtime)
        return {}

    def _local_discovery_fallback_enabled(self, session: "LeafWorkerRuntimeSession") -> bool:
        runtime = self._runtime_from_session(session)
        platform = runtime.get("platform", {})
        if isinstance(platform, dict):
            control_plane = platform.get("control_plane", {})
            if isinstance(control_plane, dict):
                configured = control_plane.get("leaf_local_discovery_fallback")
                if isinstance(configured, bool):
                    return configured
        return bool(self.allow_local_discovery_fallback)

    @staticmethod
    def _is_transport_alias(node_name: str) -> bool:
        if not isinstance(node_name, str) or not node_name:
            return False
        if node_name.startswith("system.obs."):
            return True
        if node_name.startswith("system.transport.handoff."):
            return True
        if node_name.startswith(("source:", "sink:")):
            return True
        if node_name.endswith("_bridge"):
            return True
        if "_line_bridge" in node_name:
            return True
        return False

    @staticmethod
    def _expand_known_aliases(node_names: set[str]) -> set[str]:
        aliases: set[str] = set()
        for name in node_names:
            if not isinstance(name, str) or not name:
                continue
            aliases.add(name)
            if name.endswith("-logical"):
                base = name[: -len("-logical")]
                if base:
                    aliases.add(base)
            if name.endswith("_logical"):
                base = name[: -len("_logical")]
                if base:
                    aliases.add(base)
        return aliases


__all__ = [
    "LeafRuntimeActivationService",
    "DefaultLeafRuntimeActivationService",
]
