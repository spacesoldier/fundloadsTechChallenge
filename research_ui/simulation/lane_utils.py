"""
Multi-lane pipe utilities for star/ring topology experiments.

Each process pair is connected by a LanedPipeSet: 5 duplex OS pipes, each
carrying a distinct traffic class that matches the real stream_kernel layout:

    data        — payload records (bidirectional: root→stage and stage→root)
    control     — ACKs and flow-control credits (bidirectional)
    logs        — log ObsEvents (stage→root in star; stage→obs direct in ring)
    monitoring  — metric ObsEvents (same routing as logs)
    traces      — trace ObsEvents (same routing as logs)

Usage:
    ctx  = mp.get_context("fork")
    pipe_set = LanedPipeSet.create(ctx)
    # In parent after fork:
    pipe_set.attach_parent_to_adapter(root_adapter, prefix="stage:0")
    pipe_set.close_child_ends()
    # In child process:
    pipe_set.attach_child_to_adapter(stage_adapter, prefix="root")
    pipe_set.close_parent_ends()

Target-id convention (star topology):
    Root side:   "stage:0:data", "stage:0:control", "stage:0:logs", ...
    Stage side:  "root:data",    "root:control",    "root:logs",    ...
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LANES = ("data", "control", "logs", "monitoring", "traces")


@dataclass
class LanedPipeSet:
    """5 duplex OS pipes for a single process pair.

    Each field is a (parent_conn, child_conn) tuple as returned by
    multiprocessing.context.Pipe(duplex=True).
    """
    data:       tuple
    control:    tuple
    logs:       tuple
    monitoring: tuple
    traces:     tuple

    @classmethod
    def create(cls, ctx: Any) -> "LanedPipeSet":
        """Allocate all 5 duplex pipes using the given multiprocessing context."""
        return cls(
            data       = ctx.Pipe(duplex=True),
            control    = ctx.Pipe(duplex=True),
            logs       = ctx.Pipe(duplex=True),
            monitoring = ctx.Pipe(duplex=True),
            traces     = ctx.Pipe(duplex=True),
        )

    # ------------------------------------------------------------------
    # Connection accessors
    # ------------------------------------------------------------------

    def parent_conns(self) -> dict[str, Any]:
        """Return {lane_name: parent_conn} for all 5 lanes."""
        return {
            "data":       self.data[0],
            "control":    self.control[0],
            "logs":       self.logs[0],
            "monitoring": self.monitoring[0],
            "traces":     self.traces[0],
        }

    def child_conns(self) -> dict[str, Any]:
        """Return {lane_name: child_conn} for all 5 lanes."""
        return {
            "data":       self.data[1],
            "control":    self.control[1],
            "logs":       self.logs[1],
            "monitoring": self.monitoring[1],
            "traces":     self.traces[1],
        }

    # ------------------------------------------------------------------
    # Cleanup helpers (call after fork in the appropriate process)
    # ------------------------------------------------------------------

    def close_child_ends(self) -> None:
        """Close child-side connections in the parent process after fork."""
        for conn in self.child_conns().values():
            try:
                conn.close()
            except Exception:
                pass

    def close_parent_ends(self) -> None:
        """Close parent-side connections in the child process after fork."""
        for conn in self.parent_conns().values():
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Adapter attachment helpers
    # ------------------------------------------------------------------

    def attach_child_to_adapter(self, adapter: Any, prefix: str) -> None:
        """Attach all 5 child-side connections to adapter as '{prefix}:{lane}'.

        Call inside a child process after fork.
        """
        for lane, conn in self.child_conns().items():
            adapter.attach_endpoint(conn, target_id=f"{prefix}:{lane}")

    def attach_parent_to_adapter(self, adapter: Any, prefix: str) -> None:
        """Attach all 5 parent-side connections to adapter as '{prefix}:{lane}'.

        Call in the parent (root) process after fork.
        """
        for lane, conn in self.parent_conns().items():
            adapter.attach_endpoint(conn, target_id=f"{prefix}:{lane}")
