#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


RUN_BLOCK_RE = re.compile(r"^=== RUN\s+(\d+)\s+===")
RUN_METRICS_RE = re.compile(
    r"^run\s+(\d+):\s+rc=([-0-9]+),\s+output=([0-9]+),\s+trace_all=([0-9]+),\s+trace_biz=([0-9]+)"
)
EVENT_RE = re.compile(r"\bevent=([A-Za-z0-9_.-]+)")
INT_FIELD_RE = re.compile(r"\b([a-zA-Z_]+)=(-?[0-9]+)\b")
SPAWN_RE = re.compile(
    r"event=control_plane\.lifecycle\.spawn_requested\s+group_name=([^\s]+)\s+node_count=([0-9]+)\s+nodes=(\[[^\]]*\])\s+workers=([0-9]+)"
)


@dataclass(slots=True)
class GroupSpec:
    name: str
    workers: int
    nodes: tuple[str, ...]


@dataclass(slots=True)
class RunStats:
    index: int
    rc: int | None = None
    output: int = 0
    trace_all: int = 0
    trace_biz: int = 0
    log_path: Path | None = None
    event_counts: dict[str, int] = field(default_factory=dict)
    worker_drained_total: int = 0
    boundary_stream_events: int = 0
    boundary_completed_events: int = 0
    boundary_failed_events: int = 0
    boundary_result_outputs_total: int = 0
    relay_dropped_total: int = 0
    handoff_immediate_zero_count: int = 0
    post_start_heartbeat_count: int = 0
    post_start_max_loop: int = 0
    readiness_reached: bool = False
    readiness_timeout: bool = False
    root_loop_finished: bool = False
    start_work_broadcast_dispatched: bool = False
    runtime_shutdown_started: bool = False
    fallback_stop_count: int = 0
    excerpts: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.rc == 0


@dataclass(slots=True)
class SessionReport:
    session_dir: Path
    config: str = ""
    timeout_seconds: int | None = None
    declared_runs: int | None = None
    run_stats: dict[int, RunStats] = field(default_factory=dict)
    groups: dict[str, GroupSpec] = field(default_factory=dict)



def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default



def parse_summary(summary_path: Path) -> SessionReport:
    session_dir = summary_path.parent
    report = SessionReport(session_dir=session_dir)
    current_run: RunStats | None = None
    for raw_line in summary_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Config:"):
            report.config = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Timeout per run:"):
            raw = line.split(":", 1)[1].strip().removesuffix("s")
            report.timeout_seconds = _safe_int(raw, default=0)
            continue
        if line.startswith("Runs:"):
            raw = line.split(":", 1)[1].strip().split(",", 1)[0]
            report.declared_runs = _safe_int(raw, default=0)
            continue
        block = RUN_BLOCK_RE.match(line)
        if block:
            idx = _safe_int(block.group(1), default=0)
            if idx <= 0:
                continue
            current_run = report.run_stats.setdefault(idx, RunStats(index=idx))
            continue
        metrics = RUN_METRICS_RE.match(line)
        if metrics:
            idx = _safe_int(metrics.group(1), default=0)
            if idx <= 0:
                continue
            stats = report.run_stats.setdefault(idx, RunStats(index=idx))
            stats.rc = _safe_int(metrics.group(2), default=1)
            stats.output = _safe_int(metrics.group(3))
            stats.trace_all = _safe_int(metrics.group(4))
            stats.trace_biz = _safe_int(metrics.group(5))
            current_run = stats
            continue
        if re.match(r"^[0-9]+:", line) and current_run is not None:
            current_run.excerpts.append(line)

    for idx, stats in report.run_stats.items():
        log_path = session_dir / f"run_{idx}" / "run_log_root.log"
        if log_path.exists():
            stats.log_path = log_path
            parse_run_log(log_path, stats, report)
    return report



def parse_run_log(log_path: Path, stats: RunStats, report: SessionReport) -> None:
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for event in EVENT_RE.findall(line):
            stats.event_counts[event] = stats.event_counts.get(event, 0) + 1
            if event == "control_plane.runtime.readiness_reached":
                stats.readiness_reached = True
            elif event == "control_plane.runtime.readiness_timeout":
                stats.readiness_timeout = True
            elif event == "control_plane.runtime.root_loop_finished":
                stats.root_loop_finished = True
            elif event == "control_plane.runtime.start_work_broadcast_dispatched":
                stats.start_work_broadcast_dispatched = True
            elif event == "control_plane.lifecycle.runtime_shutdown_started":
                stats.runtime_shutdown_started = True
            elif event == "control_plane.runtime.post_start_settle_heartbeat":
                stats.post_start_heartbeat_count += 1

        if "event=control_plane.reply_ingress.worker_drained" in line:
            fields = dict(INT_FIELD_RE.findall(line))
            stats.worker_drained_total += _safe_int(fields.get("drained", "0"))

        if "event=control_plane.reply_ingress.boundary_result_received" in line:
            fields = dict(INT_FIELD_RE.findall(line))
            stats.boundary_result_outputs_total += _safe_int(fields.get("outputs_total", "0"))
            if " status=stream " in f" {line} ":
                stats.boundary_stream_events += 1
            elif " status=completed " in f" {line} ":
                stats.boundary_completed_events += 1
            elif " status=failed " in f" {line} ":
                stats.boundary_failed_events += 1

        if "event=control_plane.reply_ingress.boundary_relay_normalized" in line:
            fields = dict(INT_FIELD_RE.findall(line))
            stats.relay_dropped_total += _safe_int(fields.get("relay_dropped", "0"))

        if "event=control_plane.reply_ingress.boundary_outputs_handed_off" in line:
            fields = dict(INT_FIELD_RE.findall(line))
            if _safe_int(fields.get("handoff_immediate_outputs", "0")) == 0:
                stats.handoff_immediate_zero_count += 1

        if "event=control_plane.runtime.post_start_settle_heartbeat" in line:
            fields = dict(INT_FIELD_RE.findall(line))
            stats.post_start_max_loop = max(stats.post_start_max_loop, _safe_int(fields.get("loops", "0")))

        if "event=control_plane.lifecycle.runtime_shutdown_worker_finished" in line and "fallback_used=True" in line:
            stats.fallback_stop_count += 1

        spawn = SPAWN_RE.search(line)
        if spawn:
            group_name = spawn.group(1)
            workers = _safe_int(spawn.group(4), default=1)
            try:
                parsed_nodes = ast.literal_eval(spawn.group(3))
            except Exception:
                parsed_nodes = []
            nodes: tuple[str, ...] = tuple(str(n) for n in parsed_nodes if isinstance(n, str))
            if group_name not in report.groups:
                report.groups[group_name] = GroupSpec(name=group_name, workers=workers, nodes=nodes)



def _bar(value: int, max_value: int) -> str:
    width = 0 if max_value <= 0 else int((max(0, value) / max_value) * 100)
    return f"<div class='bar'><span style='width:{width}%'></span><em>{value}</em></div>"



def _status_chip(ok: bool, text_ok: str, text_fail: str) -> str:
    cls = "ok" if ok else "bad"
    text = text_ok if ok else text_fail
    return f"<span class='chip {cls}'>{html.escape(text)}</span>"



def _render_groups_svg(groups: Iterable[GroupSpec]) -> str:
    items = list(groups)
    if not items:
        return "<p class='muted'>No spawn topology captured in logs.</p>"
    box_w = 260
    gap = 34
    pad = 20
    height = 260
    width = pad * 2 + len(items) * box_w + max(0, len(items) - 1) * gap
    parts: list[str] = [
        f"<svg class='topology' viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg'>"
    ]
    for i, g in enumerate(items):
        x = pad + i * (box_w + gap)
        y = 20
        parts.append(f"<rect x='{x}' y='{y}' width='{box_w}' height='210' rx='10' ry='10' class='group-box'/>")
        parts.append(f"<text x='{x+12}' y='{y+24}' class='group-title'>{html.escape(g.name)}</text>")
        parts.append(f"<text x='{x+12}' y='{y+44}' class='group-sub'>workers={g.workers}</text>")
        for j, node_name in enumerate(g.nodes[:8]):
            yy = y + 68 + j * 18
            parts.append(f"<text x='{x+12}' y='{yy}' class='node-name'>- {html.escape(node_name)}</text>")
        if len(g.nodes) > 8:
            yy = y + 68 + 8 * 18
            parts.append(f"<text x='{x+12}' y='{yy}' class='node-more'>... +{len(g.nodes)-8} nodes</text>")
        if i < len(items) - 1:
            x1 = x + box_w
            x2 = x + box_w + gap
            ym = y + 105
            parts.append(f"<line x1='{x1+4}' y1='{ym}' x2='{x2-4}' y2='{ym}' class='edge' marker-end='url(#arr)'/>")
    parts.append(
        "<defs><marker id='arr' markerWidth='10' markerHeight='8' refX='8' refY='4' orient='auto'>"
        "<path d='M0,0 L8,4 L0,8 z' class='arrow'/></marker></defs>"
    )
    parts.append("</svg>")
    return "".join(parts)



def render_html(report: SessionReport) -> str:
    runs = [report.run_stats[k] for k in sorted(report.run_stats)]
    max_output = max((r.output for r in runs), default=0)
    max_trace = max((r.trace_all for r in runs), default=0)

    rows: list[str] = []
    for r in runs:
        rows.append(
            "<tr>"
            f"<td>{r.index}</td>"
            f"<td>{_status_chip(r.success, 'rc=0', f'rc={r.rc}')}</td>"
            f"<td>{_bar(r.output, max_output)}</td>"
            f"<td>{_bar(r.trace_all, max_trace)}</td>"
            f"<td>{r.trace_biz}</td>"
            f"<td>{_status_chip(r.readiness_reached, 'ready', 'not ready')}</td>"
            f"<td>{_status_chip(not r.readiness_timeout, 'no timeout', 'timeout')}</td>"
            f"<td>{r.worker_drained_total}</td>"
            f"<td>{r.boundary_stream_events}/{r.boundary_completed_events}/{r.boundary_failed_events}</td>"
            f"<td>{r.handoff_immediate_zero_count}</td>"
            f"<td>{r.fallback_stop_count}</td>"
            "</tr>"
        )

    details: list[str] = []
    for r in runs:
        excerpts = "<br/>".join(html.escape(x) for x in r.excerpts[:20]) or "<span class='muted'>No summary excerpts.</span>"
        details.append(
            "<details>"
            f"<summary>Run {r.index}: key excerpts</summary>"
            f"<div class='log'>{excerpts}</div>"
            "</details>"
        )

    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>run_3x_check report</title>
<style>
  :root {{ --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#c9d1d9; --muted:#8b949e; --ok:#2ea043; --bad:#f85149; --warn:#d29922; --bar:#58a6ff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  main {{ max-width:1400px; margin:0 auto; padding:16px; }}
  h1,h2 {{ margin:8px 0 12px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; margin:10px 0; overflow:auto; }}
  .meta {{ display:grid; grid-template-columns: repeat(4,minmax(180px,1fr)); gap:10px; }}
  .meta div {{ border:1px solid var(--line); border-radius:8px; padding:8px; }}
  .k {{ color:var(--muted); display:block; }}
  table {{ width:100%; border-collapse: collapse; }}
  th, td {{ border-bottom:1px solid var(--line); padding:7px 6px; text-align:left; vertical-align:top; }}
  th {{ color:var(--muted); }}
  .chip {{ display:inline-block; border-radius:999px; padding:2px 8px; font-size:12px; border:1px solid var(--line); }}
  .chip.ok {{ color:var(--ok); border-color:rgba(46,160,67,.5); }}
  .chip.bad {{ color:var(--bad); border-color:rgba(248,81,73,.5); }}
  .bar {{ position:relative; min-width:170px; height:18px; background:#0b1320; border:1px solid #223; border-radius:8px; overflow:hidden; }}
  .bar span {{ display:block; height:100%; background:var(--bar); opacity:.75; }}
  .bar em {{ position:absolute; right:6px; top:0; font-style:normal; color:#dbe7ff; font-size:12px; line-height:18px; }}
  .muted {{ color:var(--muted); }}
  details {{ margin-top:8px; }}
  .log {{ white-space:pre-wrap; background:#0b0f14; border:1px solid var(--line); border-radius:8px; padding:8px; margin-top:6px; max-height:280px; overflow:auto; }}
  .topology {{ width:100%; min-width:600px; height:auto; }}
  .group-box {{ fill:#101722; stroke:#2f3b4e; stroke-width:1.2; }}
  .group-title {{ fill:#e6edf3; font-size:13px; font-weight:600; }}
  .group-sub {{ fill:#8b949e; font-size:11px; }}
  .node-name {{ fill:#c9d1d9; font-size:11px; }}
  .node-more {{ fill:#8b949e; font-size:11px; }}
  .edge {{ stroke:#4f6687; stroke-width:1.3; }}
  .arrow {{ fill:#4f6687; }}
</style>
</head>
<body>
<main>
  <h1>run_3x_check report</h1>
  <div class='panel meta'>
    <div><span class='k'>Session dir</span>{html.escape(str(report.session_dir))}</div>
    <div><span class='k'>Config</span>{html.escape(report.config or '(unknown)')}</div>
    <div><span class='k'>Timeout per run</span>{html.escape(str(report.timeout_seconds) if report.timeout_seconds is not None else '?')}s</div>
    <div><span class='k'>Runs parsed</span>{len(runs)}</div>
  </div>

  <div class='panel'>
    <h2>Run Stability</h2>
    <table>
      <thead>
        <tr>
          <th>run</th><th>exit</th><th>output lines</th><th>trace all</th><th>trace biz</th>
          <th>readiness</th><th>timeout</th><th>worker drained</th><th>boundary stream/ok/fail</th>
          <th>handoff immediate=0</th><th>shutdown fallback</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>

  <div class='panel'>
    <h2>Topology (from spawn_requested logs)</h2>
    {_render_groups_svg(report.groups.values())}
  </div>

  <div class='panel'>
    <h2>Key Excerpts</h2>
    {''.join(details)}
  </div>
</main>
</body>
</html>
"""



def main() -> int:
    parser = argparse.ArgumentParser(description="Render HTML report for run_3x_check archive")
    parser.add_argument("--session-dir", required=True, help="Path to run_archive/<session>_3x")
    parser.add_argument("--output", default="report.html", help="Output HTML file name (relative to session dir if not absolute)")
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    summary = session_dir / "summary.txt"
    if not summary.exists():
        raise SystemExit(f"summary.txt not found: {summary}")

    report = parse_summary(summary)
    html_text = render_html(report)

    out = Path(args.output)
    if not out.is_absolute():
        out = session_dir / out
    out.write_text(html_text, encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
