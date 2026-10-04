# AIR Dockerfile — reproducibility image for the runtime + console.
#
# NOT BUILD-VERIFIED in the author's sandbox (no Docker daemon there).
# Verify with:  docker build -t air:0.1.0 .   then run the suite inside
# the image before trusting it. Do not publish an image that has not
# been built and tested.
#
# Layout:
#   stage 1 (webbuild): Node builds the console (web/dist).
#   stage 2 (runtime):   Python 3.12 slim, pip-installs the wheel the
#                        repo builds itself, copies web/dist alongside
#                        (the console is deployed alongside the runtime,
#                        not bundled into the wheel).
#   The API serves the console at / when web/dist is present next to
#   the installed package location baked in as /app/web/dist.

# ---------------------------------------------------------------- web
FROM node:24-slim AS webbuild
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------- app
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# Build the wheel from the repo itself (hatchling; no network at runtime).
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir "."

# Console alongside the runtime (NOT bundled into the wheel: the API
# serves web/dist from <sys.prefix>/web/dist as a fallback when the
# repo layout is absent; see _serve_console).
COPY --from=webbuild /src/web/dist /tmp/web-dist
RUN PY_PREFIX=$(python -c "import sys; print(sys.prefix)") && \
    mkdir -p "$PY_PREFIX/web" && \
    cp -r /tmp/web-dist "$PY_PREFIX/web/dist" && \
    rm -rf /tmp/web-dist

# Non-root user; data dir is a volume.
RUN useradd --create-home --uid 10001 air && \
    mkdir -p /data && chown air:air /data
USER air
ENV AIR_DATA_DIR=/data
EXPOSE 8765
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).read() else 1)"

# Default: serve the API + console. Override for workers/tests.
CMD ["python", "-m", "uvicorn", "air.api.app:create_app", "--factory",
     "--host", "0.0.0.0", "--port", "8765"]
