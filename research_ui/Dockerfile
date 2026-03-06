FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    RESEARCH_UI_REPORTS_DIR=/app/research_ui/reports

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.116.1 \
    "uvicorn[standard]==0.35.0" \
    pydantic==2.12.0 \
    pyyaml==6.0.2 \
    rich==14.0.0

COPY src ./src
COPY research_ui ./research_ui

EXPOSE 8091

CMD ["python", "-m", "uvicorn", "research_ui.webapp:app", "--host", "0.0.0.0", "--port", "8091"]
