"""Minimal debug: can root attach 5 endpoints and send/recv to all of them?"""
from __future__ import annotations
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stream_kernel.execution.transport.carriers.ipc.ipc_adapters import PipeExecutionIpcTransportAdapter
from stream_kernel.execution.transport.ipc.ipc_transport import ExecutionIpcMessage


def leaf_main(leaf_id: int, child_conn: object, result_q: mp.Queue, n_expected: int) -> None:
    print(f"  [leaf#{leaf_id} pid={os.getpid()}] started", flush=True)
    adapter = PipeExecutionIpcTransportAdapter(
        context=mp.get_context("fork"),
        poll_interval_seconds=0.005,
    )
    adapter.attach_endpoint(child_conn, target_id="root")

    received = 0
    deadline = time.monotonic() + 10.0
    while received < n_expected and time.monotonic() < deadline:
        msg = adapter.recv_buffered("root", timeout=0.1)
        if msg is not None:
            received += 1

    print(f"  [leaf#{leaf_id}] received={received}/{n_expected}", flush=True)
    adapter.close()
    result_q.put({"leaf_id": leaf_id, "received": received})
    print(f"  [leaf#{leaf_id}] put to result_q, exiting", flush=True)


def main() -> None:
    n_leaves = 5
    n_messages_per_leaf = 20
    ctx = mp.get_context("fork")

    result_q: mp.Queue = ctx.Queue()
    pipes = []
    for _ in range(n_leaves):
        parent_conn, child_conn = ctx.Pipe(duplex=True)  # duplex, same as real sim
        pipes.append((parent_conn, child_conn))

    procs = []
    for i, (_, child_conn) in enumerate(pipes):
        leaf_id = i + 1
        proc = ctx.Process(
            target=leaf_main,
            args=(leaf_id, child_conn, result_q, n_messages_per_leaf),
            daemon=True,
        )
        proc.start()
        procs.append(proc)
        print(f"[root] started leaf#{leaf_id} pid={proc.pid}", flush=True)

    for _, child_conn in pipes:
        child_conn.close()

    print("[root] creating adapter...", flush=True)
    adapter = PipeExecutionIpcTransportAdapter(
        context=ctx,
        poll_interval_seconds=0.005,
    )
    for i, (parent_conn, _) in enumerate(pipes):
        adapter.attach_endpoint(parent_conn, target_id=f"leaf#{i+1}")
    print(f"[root] attached {n_leaves} endpoints", flush=True)

    time.sleep(0.2)

    print("[root] sending...", flush=True)
    for msg_id in range(n_messages_per_leaf * n_leaves):
        leaf_idx = (msg_id % n_leaves) + 1
        adapter.send(f"leaf#{leaf_idx}", f"msg-{msg_id}", no_reply=True)
    print("[root] all messages enqueued", flush=True)

    time.sleep(1.0)  # give leaves time to receive

    print("[root] joining procs...", flush=True)
    for i, proc in enumerate(procs):
        print(f"[root] joining leaf#{i+1}...", flush=True)
        proc.join(timeout=5.0)
        if proc.is_alive():
            print(f"[root] leaf#{i+1} still alive — killing", flush=True)
            proc.kill()
            proc.join(timeout=1.0)
        print(f"[root] leaf#{i+1} done (alive={proc.is_alive()})", flush=True)

    print("[root] closing adapter...", flush=True)
    adapter.close()
    print("[root] adapter closed", flush=True)

    print("[root] collecting results...", flush=True)
    for i in range(n_leaves):
        print(f"[root] waiting for result {i+1}/{n_leaves}...", flush=True)
        try:
            r = result_q.get(timeout=2.0)
            print(f"[root] got result: {r}", flush=True)
        except Exception as e:
            print(f"[root] result timeout: {e}", flush=True)

    print("[root] DONE", flush=True)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
