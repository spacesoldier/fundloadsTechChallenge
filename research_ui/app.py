from __future__ import annotations

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("research_ui.webapp:app", host="127.0.0.1", port=8091, reload=True)
