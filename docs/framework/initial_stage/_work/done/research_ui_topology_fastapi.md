# Research UI: FastAPI Topology Viewer

## Goal
Provide a lightweight local UI that renders runtime topology from:
- discovery modules
- validated newgen config
- runtime process groups

The UI is diagnostic-only: it does not start multiprocess runtime.

## Implementation
- API app: `research_ui/webapp.py`
- Topology snapshot builder: `research_ui/topology_snapshot.py`
- Local launcher: `research_ui/app.py`

### API endpoints
- `GET /health`
- `GET /api/topology?config_path=<path>`
- `GET /` interactive SVG viewer

## What is visualized
- Root process and leaf worker processes
- Process-local node sets (system + configured business nodes)
- IPC links root <-> leaf with lane labels (`control`, `data`, `observability`)
- Buffer markers on links/processes (diagnostic model)
- Discovery catalogs for services/adapters (side panel)

## UI interactions
- Mouse wheel: zoom
- Drag on background: pan
- Drag node rectangles: local layout adjustments

## Run
1. Install optional dependencies:
   - `poetry install --with research-ui`
2. Run server:
   - `PYTHONPATH=src poetry run uvicorn research_ui.webapp:app --host 127.0.0.1 --port 8091 --reload`
   - or `./scripts/run_research_ui.sh`
3. Open:
   - `http://127.0.0.1:8091`

## Docker run
- Build and run:
  - `docker compose -f research_ui/docker-compose.yml up --build`
- Open:
  - `http://127.0.0.1:8091`
- Reports list on `/` reads HTML files from:
  - `research_ui/reports/`

## Notes
- This is a structural view derived from discovery/config/runtime wiring metadata.
- It is intentionally non-invasive and safe for repeated local diagnostics.
