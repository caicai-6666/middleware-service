FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

# 先安装依赖，以便代码变化时复用依赖层。
COPY requirement.txt ./
RUN pip install --no-cache-dir -r requirement.txt

RUN groupadd --system app && useradd --system --gid app --home-dir /workspace app
COPY --chown=app:app . .
RUN mkdir -p /workspace/data/ingestion-request/file /workspace/data/deletion-request/file \
    && chown app:app /workspace/data/ingestion-request/file /workspace/data/deletion-request/file
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/middleware-service/api/health', timeout=3).close()"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
