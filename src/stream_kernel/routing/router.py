from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from stream_kernel.routing.envelope import Envelope


@dataclass(frozen=True, slots=True)
class RoutingResult:
    # Structured routing outcome used by execution engine.
    local_deliveries: list[tuple[str, object]]
    boundary_deliveries: list[object] = field(default_factory=list)
    terminal_outputs: list[object] = field(default_factory=list)

    def __iter__(self):
        # Compatibility: allow legacy `for target, payload in router.route(...)`.
        return iter(self.local_deliveries)

    def __len__(self) -> int:
        # Compatibility: allow `len(router.route(...))`.
        return len(self.local_deliveries)

    def __bool__(self) -> bool:
        return bool(self.local_deliveries)

    def __eq__(self, other: object) -> bool:
        # Compatibility: keep existing list-based assertions stable during migration.
        if isinstance(other, list):
            return self.local_deliveries == other
        if isinstance(other, RoutingResult):
            return (
                self.local_deliveries == other.local_deliveries
                and self.boundary_deliveries == other.boundary_deliveries
                and self.terminal_outputs == other.terminal_outputs
            )
        return NotImplemented

    def as_pairs(self) -> list[tuple[str, object]]:
        return list(self.local_deliveries)


@dataclass(frozen=True, slots=True)
class Router:
    # Routes outputs to consumers based on type or explicit target (Routing semantics §1–§3).
    consumers: dict[type, list[str]]
    strict: bool = True
    _known_nodes: frozenset[str] = field(init=False, default=frozenset())

    def __post_init__(self) -> None:
        # Build a deterministic set of known node names from the consumer registry.
        names: set[str] = set()
        for node_list in self.consumers.values():
            names.update(node_list)
        object.__setattr__(self, "_known_nodes", frozenset(names))

    def route(self, outputs: Iterable[object], *, source: str | None = None) -> RoutingResult:
        # Convert outputs into (target, payload) deliveries (Routing semantics §5).
        deliveries: list[tuple[str, object]] = []
        for output in outputs:
            envelope = self._normalize(output)
            payload = envelope.payload
            payload_type = type(payload)
            consumers = self.consumers.get(payload_type, [])

            if envelope.target is not None:
                # Targeted routing bypasses type fan‑out (Routing semantics §2).
                for target in self._resolve_targets(envelope.target):
                    if target not in self._known_nodes:
                        # Unknown target is a strict‑mode error (Routing semantics §5.8).
                        if self.strict:
                            raise ValueError(f"Unknown target '{target}'")
                        continue
                    if target not in consumers:
                        # Target exists but does not consume this payload type (Routing semantics §5.9).
                        if self.strict:
                            raise ValueError(
                                f"Target '{target}' does not consume '{payload_type.__name__}'"
                            )
                        continue
                    deliveries.append((target, payload))
                continue

            if not consumers:
                # No consumers: behavior is strict error vs drop (Routing roadmap, undecided).
                if self.strict:
                    raise ValueError(f"No consumers registered for '{payload_type.__name__}'")
                continue

            if source is not None:
                # Default routing cannot silently self-loop when source is the only consumer.
                filtered = [target for target in consumers if target != source]
                if not filtered:
                    if self.strict:
                        raise ValueError(
                            f"Self-loop for '{payload_type.__name__}' requires explicit target"
                        )
                    continue
                consumers = filtered

            # Default fan‑out by type (Routing semantics §5.1).
            for target in consumers:
                deliveries.append((target, payload))
        return RoutingResult(local_deliveries=deliveries)

    @staticmethod
    def _normalize(output: object) -> Envelope:
        # Accept bare payloads or pre-wrapped envelopes (Routing semantics §1).
        if isinstance(output, Envelope):
            return output
        return Envelope(payload=output)

    @staticmethod
    def _resolve_targets(target: str | Sequence[str]) -> list[str]:
        # Normalize target into a list of node names (Routing semantics §5.3).
        if isinstance(target, str):
            return [target]
        return list(target)
