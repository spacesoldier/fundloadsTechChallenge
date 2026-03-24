# Golden Example: `@adapter` for a Custom Port

Goal: show how app adapters implement a port interface used by services/nodes.

## App files

```text
src/my_app/domain/ports.py
src/my_app/adapters/customer_profile_http.py
```

`src/my_app/domain/ports.py`
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class CustomerProfilePort(Protocol):
    def segment_of(self, customer_id: str) -> str: ...
```

`src/my_app/adapters/customer_profile_http.py`
```python
from dataclasses import dataclass
from stream_kernel.adapters.contracts import adapter
from my_app.domain.ports import CustomerProfilePort

@dataclass(frozen=True, slots=True)
class CustomerProfileHttpSettings:
    base_url: str
    timeout_ms: int = 200

@dataclass(slots=True)
class HttpCustomerProfileAdapter(CustomerProfilePort):
    settings: CustomerProfileHttpSettings

    def segment_of(self, customer_id: str) -> str:
        # Example only: replace with real HTTP client
        if customer_id.startswith("VIP"):
            return "vip"
        return "retail"

@adapter(
    name="my_app.customer_profile_http",
    kind="external.customer_profile",
    consumes=[],
    emits=[],
    binds=[("stream", CustomerProfilePort)],
    execution_mode="sync",
)
def customer_profile_http_adapter(settings: dict[str, object]) -> CustomerProfilePort:
    if "base_url" not in settings:
        raise ValueError("my_app.customer_profile_http.settings.base_url is required")
    return HttpCustomerProfileAdapter(
        CustomerProfileHttpSettings(
            base_url=str(settings["base_url"]),
            timeout_ms=int(settings.get("timeout_ms", 200)),
        )
    )
```

## Config example

```yaml
runtime:
  adapters:
    streams:
      - kind: my_app.customer_profile_http
        settings:
          base_url: http://profiles.internal
          timeout_ms: 250
```

## Why this pattern

- business service injects `CustomerProfilePort`, not concrete adapter class
- adapter can be replaced (HTTP, Redis, mock) with config only
- node/service code stays unchanged
