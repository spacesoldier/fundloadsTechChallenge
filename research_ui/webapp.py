from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from research_ui.reports_catalog import list_reports
from research_ui.topology_snapshot import build_topology_snapshot

_DEFAULT_CFG = "src/fund_load/experiment_config_newgen_multiprocess_jaeger.yml"
_REPORTS_DIR = Path(os.getenv("RESEARCH_UI_REPORTS_DIR", "research_ui/reports")).resolve()

app = FastAPI(title="Stream Kernel Topology UI", version="0.2.0")
app.mount(
    "/reports",
    StaticFiles(directory=str(_REPORTS_DIR), check_dir=False),
    name="reports",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/topology")
def api_topology(
    config_path: str = Query(
        default=_DEFAULT_CFG,
        description="Path to validated newgen config file",
    ),
) -> dict[str, object]:
    try:
        return build_topology_snapshot(config_path)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"topology build failed: {exc}") from exc


@app.get("/api/reports")
def api_reports() -> dict[str, object]:
    return {
        "reports_dir": str(_REPORTS_DIR),
        "reports": list_reports(_REPORTS_DIR),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Topology UI</title>
  <style>
    :root {{
      --line: #2a3650;
      --fg: #d8e1f0;
      --muted: #8ea0c1;
      --platform: #5ec2ff;
      --project: #7de08d;
      --system: #f8c76d;
      --missing: #f57f7f;
      --buffer: #d4a5ff;
      --service: #ffd166;
      --adapter: #5eead4;
      --store: #f9a8d4;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: linear-gradient(160deg, #060a14 0%, #0d1426 100%);
      color: var(--fg);
      font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 10px;
      padding: 10px;
      height: 100vh;
    }}
    .panel {{
      background: rgba(14, 21, 35, 0.94);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      overflow: auto;
    }}
    .left input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 8px;
      background: #0b1322;
      color: var(--fg);
    }}
    .left button {{
      margin-top: 8px;
      width: 100%;
      border: 1px solid #3a5178;
      border-radius: 8px;
      padding: 7px 8px;
      background: #1a2c4a;
      color: #dce9ff;
      cursor: pointer;
    }}
    .left button:hover {{ background: #20355a; }}
    .meta {{ margin-top: 10px; color: var(--muted); }}
    .legend span {{
      display: inline-block;
      margin: 2px 6px 2px 0;
      padding: 1px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
    }}
    .platform {{ border-color: var(--platform); color: var(--platform); }}
    .project {{ border-color: var(--project); color: var(--project); }}
    .system {{ border-color: var(--system); color: var(--system); }}
    .missing {{ border-color: var(--missing); color: var(--missing); }}
    .buffer {{ border-color: var(--buffer); color: var(--buffer); }}
    .service {{ border-color: var(--service); color: var(--service); }}
    .adapter {{ border-color: var(--adapter); color: var(--adapter); }}
    .store {{ border-color: var(--store); color: var(--store); }}
    .canvas-wrap {{
      position: relative;
      height: 100%;
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid var(--line);
      background: radial-gradient(circle at 20% 20%, #15213b, #0c1324 70%);
    }}
    svg {{ width: 100%; height: 100%; display: block; user-select: none; }}
    .hint {{
      position: absolute;
      top: 10px;
      right: 10px;
      color: var(--muted);
      background: rgba(10,14,25,0.7);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 5px 8px;
      font-size: 12px;
    }}
    details {{ margin-top: 8px; }}
    summary {{ cursor: pointer; color: #cdd9ef; }}
    ul {{ margin: 6px 0 0 16px; padding: 0; }}
  </style>
</head>
<body>
  <div class="layout">
    <div class="panel left">
      <h2>Topology UI</h2>
      <label for="cfg">Config path</label>
      <input id="cfg" value="{_DEFAULT_CFG}" />
      <button id="reload">Build topology</button>
      <div class="meta" id="meta"></div>
      <div class="legend">
        <span class="platform">platform</span>
        <span class="project">project</span>
        <span class="system">system</span>
        <span class="service">service</span>
        <span class="adapter">adapter</span>
        <span class="store">store</span>
        <span class="buffer">pipe buffer</span>
        <span class="missing">missing</span>
      </div>
      <details open>
        <summary>Discovered services</summary>
        <ul id="services"></ul>
      </details>
      <details>
        <summary>Discovered adapters</summary>
        <ul id="adapters"></ul>
      </details>
      <details>
        <summary>Discovered stores</summary>
        <ul id="stores"></ul>
      </details>
      <details open>
        <summary>Run reports</summary>
        <div class="meta" id="reports-dir"></div>
        <ul id="reports"></ul>
      </details>
    </div>
    <div class="panel canvas-wrap">
      <div class="hint">
        Wheel: zoom, drag background: pan, drag process frame: move whole group,
        drag item: move item
      </div>
      <svg id="topo" viewBox="0 0 3600 2200">
        <defs>
          <marker id="arr" markerWidth="10" markerHeight="8" refX="8" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#7f95bc" />
          </marker>
        </defs>
        <g id="viewport"></g>
      </svg>
    </div>
  </div>
<script>
(() => {{
  const svg = document.getElementById("topo");
  const viewport = document.getElementById("viewport");
  const cfgInput = document.getElementById("cfg");
  const reloadBtn = document.getElementById("reload");
  const meta = document.getElementById("meta");
  const servicesEl = document.getElementById("services");
  const adaptersEl = document.getElementById("adapters");
  const storesEl = document.getElementById("stores");
  const reportsDirEl = document.getElementById("reports-dir");
  const reportsEl = document.getElementById("reports");

  const state = {{
    scale: 1,
    panX: 0,
    panY: 0,
    draggingNode: null,
    draggingProcess: null,
    draggingCanvas: false,
    dragStartSceneX: 0,
    dragStartSceneY: 0,
    dragOrigX: 0,
    dragOrigY: 0,
    processDragOrigins: new Map(),
    nodePos: new Map(),
    processMembers: new Map(),
    edges: [],
    edgeEls: [],
  }};

  function esc(value) {{
    const text = String(value ?? "");
    return text.replace(/[&<>\"]/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
  }}

  function applyViewportTransform() {{
    const t = `translate(${{state.panX}} ${{state.panY}}) scale(${{state.scale}})`;
    viewport.setAttribute("transform", t);
  }}

  function pointerToScene(clientX, clientY) {{
    const pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const ctm = viewport.getScreenCTM();
    if (!ctm) return {{ x: clientX, y: clientY }};
    const local = pt.matrixTransform(ctm.inverse());
    return {{ x: local.x, y: local.y }};
  }}

  function clearCanvas() {{
    while (viewport.firstChild) viewport.removeChild(viewport.firstChild);
    state.nodePos.clear();
    state.processMembers.clear();
    state.edges = [];
    state.edgeEls = [];
  }}

  function originColor(origin, kind) {{
    if (kind === "buffer") return "#d4a5ff";
    if (kind === "service") return "#ffd166";
    if (kind === "adapter") return "#5eead4";
    if (kind === "store") return "#f9a8d4";
    if (origin === "platform") return "#5ec2ff";
    if (origin === "project") return "#7de08d";
    if (origin === "missing") return "#f57f7f";
    return "#f8c76d";
  }}

  function edgeColor(kind) {{
    if (kind === "service") return "#ffd166";
    if (kind === "adapter") return "#5eead4";
    if (kind === "store") return "#f9a8d4";
    return "#8096bd";
  }}

  function registerProcessMember(processId, entityId) {{
    if (!state.processMembers.has(processId)) state.processMembers.set(processId, []);
    state.processMembers.get(processId).push(entityId);
  }}

  function addProcessBox(proc, x, y, w, h) {{
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("data-proc", proc.process_id);

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", y);
    rect.setAttribute("width", w);
    rect.setAttribute("height", h);
    rect.setAttribute("rx", 12);
    rect.setAttribute("ry", 12);
    rect.setAttribute("fill", "#101a2c");
    rect.setAttribute("stroke", "#3a4f73");
    rect.setAttribute("stroke-width", "1.3");

    const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
    title.setAttribute("x", x + 10);
    title.setAttribute("y", y + 20);
    title.setAttribute("fill", "#dce7fb");
    title.setAttribute("font-size", "13");
    const n = (proc.nodes || []).length;
    const s = (proc.services || []).length;
    const a = (proc.adapters || []).length;
    const st = (proc.stores || []).length;
    title.textContent = `${{proc.process_id}}  [N:${{n}} S:${{s}} A:${{a}} ST:${{st}}]`;

    g.appendChild(rect);
    g.appendChild(title);
    g.addEventListener("mousedown", (e) => {{
      e.stopPropagation();
      state.draggingProcess = proc.process_id;
      const p = pointerToScene(e.clientX, e.clientY);
      state.dragStartSceneX = p.x;
      state.dragStartSceneY = p.y;
      const origins = new Map();
      const processPos = state.nodePos.get(proc.process_id);
      if (processPos) origins.set(proc.process_id, {{ x: processPos.x, y: processPos.y }});
      for (const memberId of state.processMembers.get(proc.process_id) || []) {{
        const pos = state.nodePos.get(memberId);
        if (pos) origins.set(memberId, {{ x: pos.x, y: pos.y }});
      }}
      state.processDragOrigins = origins;
    }});

    viewport.appendChild(g);
    state.nodePos.set(proc.process_id, {{
      x: x + w / 2,
      y: y + h / 2,
      kind: "process",
      group: g,
      rect,
      text: title,
    }});
    if (!state.processMembers.has(proc.process_id)) state.processMembers.set(proc.process_id, []);
  }}

  function addEntity(entityId, label, x, y, origin, kind, processId) {{
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("data-entity-id", entityId);
    g.style.cursor = "move";

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    const width = Math.max(110, Math.min(340, label.length * 6.2 + 24));
    const height = 24;
    rect.setAttribute("x", x - width / 2);
    rect.setAttribute("y", y - height / 2);
    rect.setAttribute("width", width);
    rect.setAttribute("height", height);
    rect.setAttribute("rx", 8);
    rect.setAttribute("ry", 8);
    rect.setAttribute("fill", "#0b1323");
    rect.setAttribute("stroke", originColor(origin, kind));
    rect.setAttribute("stroke-width", "1.2");

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", x);
    text.setAttribute("y", y + 4);
    text.setAttribute("fill", "#d8e1f0");
    text.setAttribute("font-size", "11");
    text.setAttribute("text-anchor", "middle");
    text.textContent = label;

    g.appendChild(rect);
    g.appendChild(text);
    g.addEventListener("mousedown", (e) => {{
      e.stopPropagation();
      state.draggingNode = entityId;
      const p = pointerToScene(e.clientX, e.clientY);
      state.dragStartSceneX = p.x;
      state.dragStartSceneY = p.y;
      const pos = state.nodePos.get(entityId);
      state.dragOrigX = pos.x;
      state.dragOrigY = pos.y;
    }});

    viewport.appendChild(g);
    state.nodePos.set(entityId, {{ x, y, kind, group: g, rect, text }});
    if (processId) registerProcessMember(processId, entityId);
  }}

  function addEdge(fromId, toId, options) {{
    state.edges.push({{
      fromId,
      toId,
      dashed: Boolean(options?.dashed),
      color: options?.color || "#8096bd",
    }});
  }}

  function redrawEdges() {{
    for (const el of state.edgeEls) el.remove();
    state.edgeEls = [];
    for (const edge of state.edges) {{
      const a = state.nodePos.get(edge.fromId);
      const b = state.nodePos.get(edge.toId);
      if (!a || !b) continue;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", a.x);
      line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x);
      line.setAttribute("y2", b.y);
      line.setAttribute("stroke", edge.color);
      line.setAttribute("stroke-width", "1.1");
      if (edge.dashed) line.setAttribute("stroke-dasharray", "5 4");
      line.setAttribute("marker-end", "url(#arr)");
      viewport.insertBefore(line, viewport.firstChild);
      state.edgeEls.push(line);
    }}
  }}

  function updateEntityPosition(entityId, x, y, redraw = true) {{
    const node = state.nodePos.get(entityId);
    if (!node || !node.rect || !node.text) return;
    const w = parseFloat(node.rect.getAttribute("width"));
    const h = parseFloat(node.rect.getAttribute("height"));
    node.x = x;
    node.y = y;
    node.rect.setAttribute("x", x - w / 2);
    node.rect.setAttribute("y", y - h / 2);
    node.text.setAttribute("x", x);
    node.text.setAttribute("y", y + 4);
    if (redraw) redrawEdges();
  }}

  function updateProcessPosition(processId, x, y) {{
    const proc = state.nodePos.get(processId);
    if (!proc || !proc.rect || !proc.text) return;
    const w = parseFloat(proc.rect.getAttribute("width"));
    const h = parseFloat(proc.rect.getAttribute("height"));
    proc.x = x;
    proc.y = y;
    proc.rect.setAttribute("x", x - w / 2);
    proc.rect.setAttribute("y", y - h / 2);
    proc.text.setAttribute("x", x - w / 2 + 10);
    proc.text.setAttribute("y", y - h / 2 + 20);
  }}

  function renderCatalog(listEl, rows, formatter) {{
    listEl.innerHTML = "";
    for (const row of rows) {{
      const li = document.createElement("li");
      li.innerHTML = formatter(row);
      listEl.appendChild(li);
    }}
  }}

  function renderReports(payload) {{
    reportsDirEl.textContent = `dir: ${{payload.reports_dir || "-"}}`;
    reportsEl.innerHTML = "";
    const reports = payload.reports || [];
    if (!reports.length) {{
      const li = document.createElement("li");
      li.textContent = "No reports found";
      reportsEl.appendChild(li);
      return;
    }}
    for (const report of reports) {{
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = `/reports/${{encodeURIComponent(report.name)}}`;
      a.target = "_blank";
      a.rel = "noopener";
      a.style.color = "#9dc2ff";
      a.textContent = report.name;
      const suffix = document.createElement("span");
      suffix.style.color = "#8ea0c1";
      suffix.textContent = ` (${{report.size_bytes}} bytes)`;
      li.appendChild(a);
      li.appendChild(suffix);
      reportsEl.appendChild(li);
    }}
  }}

  function renderProcessEntities(proc, x, y, procW, procH) {{
    const centerX = x + procW / 2;
    const startY = y + 34;
    const stepY = 24;

    const renderColumn = (items, colX) => {{
      (items || []).forEach((item, idx) => {{
        const entityId = item.id || `${{proc.process_id}}:${{item.kind || "entity"}}:${{idx}}`;
        addEntity(
          entityId,
          item.name,
          colX,
          startY + idx * stepY,
          item.origin || "platform",
          item.kind || "business",
          proc.process_id,
        );
        addEdge(proc.process_id, entityId, {{ dashed: true, color: "#4f607f" }});
      }});
    }};

    renderColumn(proc.nodes, centerX - 180);
    renderColumn(proc.services, centerX - 45);
    renderColumn(proc.adapters, centerX + 90);
    renderColumn(proc.stores, centerX + 225);

    (proc.links || []).forEach((link) => {{
      addEdge(link.from, link.to, {{ color: edgeColor(link.kind) }});
    }});
  }}

  function renderTopology(data) {{
    clearCanvas();
    meta.innerHTML = `
      <div>config: ${{esc(data.meta.config_path)}}</div>
      <div>discovery modules: ${{data.meta.discovery_modules.length}}</div>
      <div>processes: ${{data.meta.process_count}}, pipes: ${{data.meta.pipe_count}}</div>
      <div>transport: ${{esc(data.meta.transport)}}</div>
    `;

    renderCatalog(
      servicesEl,
      data.catalog.services || [],
      (row) =>
        `<span class="${{esc(row.origin)}}">${{esc(row.origin)}}</span> ` +
        `${{esc(row.name)}} <span style="color:#8ea0c1">(${{esc(row.module)}})</span>`
    );
    renderCatalog(
      adaptersEl,
      data.catalog.adapters || [],
      (row) =>
        `<span class="${{esc(row.origin)}}">${{esc(row.origin)}}</span> ` +
        `${{esc(row.name)}} :: ${{esc(row.kind || "adapter")}}`
    );
    renderCatalog(
      storesEl,
      data.catalog.stores || [],
      (row) =>
        `<span class="store">store</span> ${{esc(row.name)}}`
    );

    const processes = data.processes || [];
    const root = processes.find((proc) => proc.process_id === "root");
    const leaves = processes.filter((proc) => proc.process_id !== "root");

    const procW = 520;
    const rowGap = 60;
    const colGap = 40;
    const cols = Math.max(1, Math.ceil(Math.sqrt(leaves.length || 1)));

    if (root) {{
      const rootMax = Math.max(
        root.nodes?.length || 0,
        root.services?.length || 0,
        root.adapters?.length || 0,
        root.stores?.length || 0,
        1,
      );
      const rootH = Math.max(180, 70 + rootMax * 26);
      addProcessBox(root, 1540, 40, procW, rootH);
      renderProcessEntities(root, 1540, 40, procW, rootH);
    }}

    leaves.forEach((proc, i) => {{
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = 120 + col * (procW + colGap);
      const y = 360 + row * (380 + rowGap);
      const maxRows = Math.max(
        proc.nodes?.length || 0,
        proc.services?.length || 0,
        proc.adapters?.length || 0,
        proc.stores?.length || 0,
        1,
      );
      const procH = Math.max(180, 70 + maxRows * 26);
      addProcessBox(proc, x, y, procW, procH);
      renderProcessEntities(proc, x, y, procW, procH);
    }});

    (data.pipes || []).forEach((pipe, idx) => {{
      const fromId = pipe.from_process;
      const toId = pipe.to_process;
      const a = state.nodePos.get(fromId);
      const b = state.nodePos.get(toId);
      if (!a || !b) return;
      const bx = (a.x + b.x) / 2 + ((idx % 2) ? 44 : -44);
      const by = (a.y + b.y) / 2;
      const bufferId = `pipe:${{pipe.pipe_id}}`;
      const pipeLabel = `${{pipe.transport}} [${{(pipe.lanes || []).join(",")}}]`;
      addEntity(bufferId, pipeLabel, bx, by, "platform", "buffer", null);
      addEdge(fromId, bufferId, {{ color: "#9da6ff" }});
      addEdge(bufferId, toId, {{ color: "#9da6ff" }});
      addEdge(toId, bufferId, {{ dashed: true, color: "#6e77a8" }});
      addEdge(bufferId, fromId, {{ dashed: true, color: "#6e77a8" }});
    }});

    redrawEdges();
  }}

  async function loadTopology() {{
    const cfg = cfgInput.value.trim();
    const res = await fetch(`/api/topology?config_path=${{encodeURIComponent(cfg)}}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "request failed");
    renderTopology(body);
  }}

  async function loadReports() {{
    const res = await fetch("/api/reports");
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "reports request failed");
    renderReports(body);
  }}

  reloadBtn.addEventListener("click", async () => {{
    try {{
      await loadTopology();
      await loadReports();
    }} catch (err) {{
      alert(`Topology build failed: ${{err.message}}`);
    }}
  }});

  svg.addEventListener("wheel", (e) => {{
    e.preventDefault();
    const delta = e.deltaY < 0 ? 1.08 : 0.92;
    state.scale = Math.min(4, Math.max(0.25, state.scale * delta));
    applyViewportTransform();
  }}, {{ passive: false }});

  svg.addEventListener("mousedown", (e) => {{
    if (state.draggingNode || state.draggingProcess) return;
    state.draggingCanvas = true;
    const p = pointerToScene(e.clientX, e.clientY);
    state.dragStartSceneX = p.x;
    state.dragStartSceneY = p.y;
    state.dragOrigX = state.panX;
    state.dragOrigY = state.panY;
  }});

  window.addEventListener("mousemove", (e) => {{
    const p = pointerToScene(e.clientX, e.clientY);
    if (state.draggingNode) {{
      const dx = p.x - state.dragStartSceneX;
      const dy = p.y - state.dragStartSceneY;
      updateEntityPosition(state.draggingNode, state.dragOrigX + dx, state.dragOrigY + dy, true);
      return;
    }}

    if (state.draggingProcess) {{
      const dx = p.x - state.dragStartSceneX;
      const dy = p.y - state.dragStartSceneY;
      for (const [id, pos] of state.processDragOrigins.entries()) {{
        const nx = pos.x + dx;
        const ny = pos.y + dy;
        if (id === state.draggingProcess) updateProcessPosition(id, nx, ny);
        else updateEntityPosition(id, nx, ny, false);
      }}
      redrawEdges();
      return;
    }}

    if (state.draggingCanvas) {{
      const dx = p.x - state.dragStartSceneX;
      const dy = p.y - state.dragStartSceneY;
      state.panX = state.dragOrigX + dx;
      state.panY = state.dragOrigY + dy;
      applyViewportTransform();
    }}
  }});

  window.addEventListener("mouseup", () => {{
    state.draggingCanvas = false;
    state.draggingNode = null;
    state.draggingProcess = null;
    state.processDragOrigins = new Map();
  }});

  applyViewportTransform();
  loadTopology().catch((err) => alert(`Initial topology load failed: ${{err.message}}`));
  loadReports().catch((err) => alert(`Report list load failed: ${{err.message}}`));
}})();
</script>
</body>
</html>
"""
    return HTMLResponse(html)
