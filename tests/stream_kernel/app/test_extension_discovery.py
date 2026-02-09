from __future__ import annotations

from types import ModuleType

import pytest

from stream_kernel.app.extensions import ExtensionDiscoveryError, framework_discovery_modules


def test_framework_discovery_modules_collects_extension_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Runtime should resolve platform discovery modules via extension providers, not hardcoded lists.
    provider = ModuleType("fake.provider")
    provider.discovery_modules = lambda: ["pkg.adapters", "pkg.observers"]  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "stream_kernel.app.extensions._load_extension_provider_modules",
        lambda: [provider],
    )

    modules = framework_discovery_modules()
    assert modules == ["pkg.adapters", "pkg.observers"]


def test_framework_discovery_modules_deduplicates_preserving_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Duplicate module names from providers should be collapsed deterministically.
    provider_a = ModuleType("fake.provider.a")
    provider_b = ModuleType("fake.provider.b")
    provider_a.discovery_modules = lambda: ["m.a", "m.b"]  # type: ignore[attr-defined]
    provider_b.discovery_modules = lambda: ["m.b", "m.c"]  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "stream_kernel.app.extensions._load_extension_provider_modules",
        lambda: [provider_a, provider_b],
    )

    modules = framework_discovery_modules()
    assert modules == ["m.a", "m.b", "m.c"]


def test_framework_discovery_modules_rejects_invalid_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Providers must return list[str] to keep discovery contract strict and predictable.
    provider = ModuleType("fake.provider")
    provider.discovery_modules = lambda: "not-a-list"  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "stream_kernel.app.extensions._load_extension_provider_modules",
        lambda: [provider],
    )

    with pytest.raises(ExtensionDiscoveryError):
        framework_discovery_modules()

