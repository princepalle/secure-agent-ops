# syntax=docker/dockerfile:1.7
# One image, two roles: the API (default command) and the Streamlit dashboard (compose overrides the command).

ARG PYTHON_IMAGE=python:3.12-slim

# ---- build: resolve and install locked dependencies into a virtualenv ----------------------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Optional build secret "proxy_ca": a CA bundle for building behind a TLS-intercepting proxy.
# It is mounted only for this step and never stored in an image layer.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -s /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca PIP_CERT=/run/secrets/proxy_ca; fi \
    && pip install "uv==0.11.32" \
    && uv sync --frozen --no-dev --no-install-project --extra ui --extra bedrock
# The application itself is not pip-installed: it runs from /app (uvicorn and Streamlit add it to sys.path).

# ---- runtime: no build tools, non-root user, read-only friendly ---------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="SecureAgentOps" \
      org.opencontainers.image.description="Secure multi-tenant AI agent platform (API + dashboard)"
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY frontend ./frontend
COPY sample_data ./sample_data
RUN mkdir -p /app/data && chown app:app /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SAO_DATA_DIR=/app/data HOME=/app \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false STREAMLIT_CLIENT_TOOLBAR_MODE=minimal \
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=5 STREAMLIT_THEME_PRIMARY_COLOR="#2563eb"
USER app
EXPOSE 8000 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# One worker: the rate limiter is in-process and Chroma runs embedded (see SECURITY.md / ARCHITECTURE.md).
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-server-header", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
