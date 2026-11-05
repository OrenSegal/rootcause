# rootcause telemetry MCP server: every benchmark scenario's fixture telemetry, read-only,
# over streamable HTTP. One endpoint per scenario at /scenarios/<dir>/mcp, health at /healthz.
#
# Run locally:
#   docker build -t rootcause-telemetry .
#   docker run --rm -p 8000:8000 rootcause-telemetry
#   curl -s localhost:8000/healthz
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first so a fixture or source change does not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project

COPY src ./src
COPY scenarios ./scenarios
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked \
    && chmod -R a-w /app/scenarios

USER nobody
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD ["python3", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz')"]
CMD ["/app/.venv/bin/rootcause", "telemetry-http", "--scenarios", "/app/scenarios", "--port", "8000"]
