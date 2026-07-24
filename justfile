# twin-fleet task runner. `just` for a listing.

set shell := ["bash", "-euo", "pipefail", "-c"]

# ─── setup ─────────────────────────────────────────────────────────────────

# Install dev dependencies for the whole workspace with uv.
install:
    uv sync --all-groups --all-packages
    # iCloud's fileproviderd asynchronously sets the macOS hidden flag on
    # dot-dirs; Python >= 3.12 skips hidden .pth files, silently breaking
    # editable installs (setuptools#4595). Clearing is idempotent.
    chflags -R nohidden .venv 2>/dev/null || true

# Regenerate protobuf stubs into contracts.gen (checked in — commit the diff).
gen:
    uv run python -m grpc_tools.protoc \
        --proto_path=contracts/proto \
        --python_out=contracts/src/contracts/gen \
        --grpc_python_out=contracts/src/contracts/gen \
        --mypy_out=contracts/src/contracts/gen \
        --mypy_grpc_out=contracts/src/contracts/gen \
        contracts/proto/state.proto
    # grpc_tools emits top-level imports; rewrite to package-relative so
    # the stubs work as contracts.gen.* (long-standing protoc quirk).
    uv run python -c "import pathlib; p = pathlib.Path('contracts/src/contracts/gen/state_pb2_grpc.py'); p.write_text(p.read_text().replace('import state_pb2 as', 'from . import state_pb2 as'))"

# ─── quality gates ─────────────────────────────────────────────────────────

# iCloud re-hides .pth files after every sync (see install); run before any
# uv-run recipe so editable imports never silently vanish.
_unhide:
    @chflags -R nohidden .venv 2>/dev/null || true

lint: _unhide
    uv run ruff check .
    uv run ruff format --check .

format: _unhide
    uv run ruff format .
    uv run ruff check --fix .

typecheck: _unhide
    uv run mypy contracts services simulators loadtest

test: _unhide
    uv run pytest

check: lint typecheck test

# ─── stack ─────────────────────────────────────────────────────────────────

# Build all images (5 services + the simulator under the `sim` profile).
build:
    docker compose --profile sim build

# Start infra + the five services WITHOUT robots (add robots with `just fleet`).
up:
    docker compose up -d --build
    @echo "Grafana:    http://localhost:3000 (admin/admin)"
    @echo "Prometheus: http://localhost:9090"
    @echo "Viz:        http://localhost:8004"
    @echo "Fleet:      http://localhost:8005/fleet   (empty until `just fleet`)"

# Stop the stack and remove any running robot containers.
down:
    docker rm -f $(docker ps -aq --filter "name=twin-fleet-robot-") 2>/dev/null || true
    docker compose --profile sim down

logs svc="":
    docker compose logs -f {{svc}}

# ─── fleet ───────────────────────────────────────────────────────────────────

# Start the fixed stack, then launch N robots (robot_1..robot_N) as individually
# named containers on the compose network. N is positional: `just fleet 20`.
# Templated launcher, not `--scale`: per-container so a single robot can be
# killed (ADR-0003) and each gets a unique human-readable robot_id (ADR-0002).
fleet N="5": build
    docker compose up -d
    for i in $(seq 1 {{N}}); do \
        docker run -d --rm --name twin-fleet-robot-$i \
            --network twin-fleet_default \
            -e MQTT_HOST=mosquitto -e ROBOT_ID=robot_$i -e TELEMETRY_HZ=50 \
            twin-fleet-simulator >/dev/null; \
    done
    @echo "fleet up: {{N}} robots (robot_1..robot_{{N}}) — Grafana http://localhost:3000"

# Kill one robot to demo graceful fleet degradation (it drops from /fleet).
kill-robot n:
    docker kill twin-fleet-robot-{{n}}
    @echo "killed robot_{{n}} — check: just ls-fleet"

# Show the live fleet registry.
ls-fleet:
    @curl -s localhost:8005/fleet | python3 -m json.tool

# Send every live robot home (coordinated best-effort fan-out; ADR-0004).
home:
    @curl -s -X POST localhost:8005/fleet/command \
        -H 'content-type: application/json' -d '{"kind":"home"}' | python3 -m json.tool

# ─── health ────────────────────────────────────────────────────────────────

_check name url:
    @curl -sf {{url}} >/dev/null && echo "{{name}} ✓" || echo "{{name}} ✗"

# Smoke check: infra and all five services answer.
healthz:
    @just _check grafana    http://localhost:3000/api/health
    @just _check influx     http://localhost:8086/health
    @just _check prometheus http://localhost:9090/-/healthy
    @just _check telemetry  http://localhost:8001/healthz/ready
    @just _check state      http://localhost:8002/healthz/ready
    @just _check command    http://localhost:8003/healthz/ready
    @just _check viz        http://localhost:8004/healthz/ready
    @just _check fleet      http://localhost:8005/healthz/ready

# ─── load test + demo ────────────────────────────────────────────────────────

# Ramp 1→MAX robots; record p50/p95/p99 latency, broker CPU, Influx throughput.
# Fills the ADR-0005 breakpoint table (raw runs land in loadtest/runs/).
loadtest MAX="20": _unhide
    uv run python loadtest/ramp.py --max {{MAX}}

# Record a screencast for the README (1→20 ramp). Requires peek + a display.
record:
    peek --start-timer 3 --duration 20 --output-format gif \
         --output docs/demo/twin-fleet.gif
