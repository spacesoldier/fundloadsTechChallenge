import multiprocessing as mp
import time

def child_event(ev):
    ev.wait(2)

def child_queue(q):
    q.put("pong")

def run_ctx(method):
    print(f"\n=== {method} ===")
    try:
        ctx = mp.get_context(method)
    except Exception as e:
        print("get_context FAIL:", type(e).__name__, e)
        return

    # 1) Event + Process
    try:
        ev = ctx.Event()
        p = ctx.Process(target=child_event, args=(ev,))
        p.start()
        time.sleep(0.2)
        ev.set()
        p.join(3)
        print("Event/Process:", "OK" if p.exitcode == 0 else f"FAIL exitcode={p.exitcode}")
        if p.is_alive():
            p.terminate()
            p.join()
    except Exception as e:
        print("Event/Process FAIL:", type(e).__name__, e)

    # 2) Queue + Process
    try:
        q = ctx.Queue()
        p = ctx.Process(target=child_queue, args=(q,))
        p.start()
        msg = q.get(timeout=3)
        p.join(3)
        print("Queue/Process:", "OK" if (msg == "pong" and p.exitcode == 0) else f"FAIL msg={msg} exit={p.exitcode}")
        if p.is_alive():
            p.terminate()
            p.join()
    except Exception as e:
        print("Queue/Process FAIL:", type(e).__name__, e)

if __name__ == "__main__":
    print("default start method:", mp.get_start_method(allow_none=True))
    for m in ("spawn", "fork", "forkserver"):
        run_ctx(m)
