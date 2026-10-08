FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
RUN pip install --upgrade pip

# the app runs as a uid that exists on no host account, in the host group that owns the mounted data
RUN groupadd --gid 10001 appuser && useradd --create-home --uid 10001 --gid 10001 appuser \
    && groupadd --gid 1001 hostdata && usermod -aG hostdata appuser

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser dbt ./dbt
COPY --chown=appuser:appuser ui ./ui
COPY --chown=appuser:appuser scripts ./scripts

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["sh", "-c", "umask 002 && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
