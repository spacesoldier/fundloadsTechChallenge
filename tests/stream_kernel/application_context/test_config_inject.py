from __future__ import annotations

import pytest

from stream_kernel.application_context.config_inject import ConfigValue, ConfigScope, config


def test_config_value_descriptor_carries_path_and_default() -> None:
    dep = config.value("limits.daily", default=100)
    assert isinstance(dep, ConfigValue)
    assert dep.path == "limits.daily"
    assert dep.default == 100


def test_config_value_resolves_from_mapping() -> None:
    dep = config.value("limits.daily", default=100)
    cfg = {"limits": {"daily": 200}}
    scope = ConfigScope(node_cfg=cfg, global_cfg=cfg, root_cfg=cfg)
    assert dep.resolve(scope) == 200


def test_config_value_missing_path_raises() -> None:
    dep = config.value("limits.daily")
    cfg = {"limits": {}}
    scope = ConfigScope(node_cfg=cfg, global_cfg=cfg, root_cfg=cfg)
    with pytest.raises(KeyError):
        dep.resolve(scope)


def test_config_value_uses_default_when_missing_and_default_provided() -> None:
    dep = config.value("limits.daily", default=100)
    cfg = {"limits": {}}
    scope = ConfigScope(node_cfg=cfg, global_cfg=cfg, root_cfg=cfg)
    assert dep.resolve(scope) == 100
