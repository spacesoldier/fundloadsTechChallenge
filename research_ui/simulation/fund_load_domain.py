"""Business domain types — stable module path so pickle works across processes."""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class FundLoadRecord:
    """Payload: a fund-load request entering the pipeline."""
    seq:        int
    account_id: str
    amount_usd: float
    currency:   str = "USD"
    trace_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    created_ns: int = field(default_factory=time.monotonic_ns)


@dataclass
class FundLoadResult:
    """Result after all processing nodes have run."""
    seq:            int
    account_id:     str
    amount_usd:     float
    ledger_id:      str
    status:         str          # "accepted" | "rejected"
    processing_ns:  int = field(default_factory=time.monotonic_ns)
    node_spans:     list[dict] = field(default_factory=list)
