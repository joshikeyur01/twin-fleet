# Build context is the repo root (see docker-compose.yml) so the image can
# include the shared contracts package. This file is the template for every
# Python service in the workspace.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Manifests first: dependency layers survive source-only changes. uv needs
# every workspace member's pyproject present to resolve, so copy them all
# (the upstream twin-services miss was omitting a member here).
COPY pyproject.toml uv.lock .python-version ./
COPY contracts/pyproject.toml contracts/
COPY services/telemetry-svc/pyproject.toml services/telemetry-svc/
COPY services/state-svc/pyproject.toml services/state-svc/
COPY services/command-svc/pyproject.toml services/command-svc/
COPY services/viz-svc/pyproject.toml services/viz-svc/
COPY services/fleet-svc/pyproject.toml services/fleet-svc/
COPY simulators/pyproject.toml simulators/

RUN uv sync --frozen --no-dev --no-install-workspace --package telemetry-svc

# Now the source that actually changes.
COPY contracts/ contracts/
COPY services/telemetry-svc/ services/telemetry-svc/

RUN uv sync --frozen --no-dev --package telemetry-svc

RUN useradd --create-home --uid 10001 svc
USER svc

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8001
CMD ["python", "-m", "telemetry_svc.main"]
