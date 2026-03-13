FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    RESEARCH_UI_REPORTS_DIR=/app/research_ui/reports

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.135.1 \
    "uvicorn[standard]==0.41.0" \
    pydantic==2.12.5 \
    pyyaml==6.0.3 \
    rich==14.0.0 \
    psycopg[binary]==3.3.3 \
    pymongo==4.16.0 \
    mcp==1.26.0

COPY src ./src
COPY research_ui ./research_ui

EXPOSE 8091

CMD ["python", "-m", "uvicorn", "research_ui.webapp:app", "--host", "0.0.0.0", "--port", "8091"]
