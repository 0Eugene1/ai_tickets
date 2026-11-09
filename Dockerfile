FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY data ./data
RUN python -m app.knowledge.build_runtime_bundle \
        /app/data/knowledge/runtime /app/data/knowledge/runtime \
        --reviewed-dir /app/data/knowledge/reviewed \
    && mkdir -p /app/runtime \
    && useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
