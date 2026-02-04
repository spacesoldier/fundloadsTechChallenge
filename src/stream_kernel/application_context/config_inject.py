from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConfigValue:
    # Marker for config-driven values resolved at build time.
    path: str
    default: Any = None
    has_default: bool = False

    def resolve(self, scope: "ConfigScope") -> object:
        # Support explicit global.* paths and default to node/global slice.
        if self.path.startswith("global."):
            path = self.path[len("global.") :]
            return _resolve_path(scope.global_cfg, path, self)
        return _resolve_path(scope.node_cfg, self.path, self, fallback=scope.global_cfg)


@dataclass(frozen=True, slots=True)
class ConfigScope:
    node_cfg: dict[str, object]
    global_cfg: dict[str, object]
    root_cfg: dict[str, object]


def _resolve_path(cfg: dict[str, object], path: str, ref: ConfigValue, fallback: dict[str, object] | None = None) -> object:
    parts = path.split(".")
    cur: object = cfg
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            if fallback is not None:
                return _resolve_path(fallback, path, ref, fallback=None)
            if ref.has_default:
                return ref.default
            raise KeyError(ref.path)
        cur = cur[part]
    return cur


class _ConfigFactory:
    # Convenience helper: config.value("a.b", default=...)
    def value(self, path: str, default: object | None = None) -> ConfigValue:
        if default is None:
            return ConfigValue(path=path, default=None, has_default=False)
        return ConfigValue(path=path, default=default, has_default=True)


config = _ConfigFactory()
