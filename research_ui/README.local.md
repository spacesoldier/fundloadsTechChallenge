# Research UI Local Dev

## 1) Start backing services only

```bash
docker compose -f research_ui/docker-compose.yml up -d postgres mongodb
```

`research_ui` service is under profile `docker-ui`, so it is not started by default.

If you need Docker UI service:

```bash
docker compose -f research_ui/docker-compose.yml --profile docker-ui up -d research_ui
```

## 2) Install local Python deps for UI

```bash
cd research_ui
poetry install
```

## 3) Run FastAPI locally with hot reload

From repo root:

```bash
./research_ui/run_local_ui.sh
```

## 4) Useful env overrides

```bash
RESEARCH_UI_REDIS_HOST=127.0.0.1
RESEARCH_UI_REDIS_PORT=6379
RESEARCH_UI_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/research_ui
RESEARCH_UI_MONGO_URI=mongodb://127.0.0.1:27017
RESEARCH_UI_LOG_LEVEL=DEBUG
```
