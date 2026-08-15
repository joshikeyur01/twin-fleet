"""1 -> N load ramp — the ADR-0005 breakpoint driver.

Runs against a live stack (`just up`) and orchestrates the fleet itself: at each
ramp step it launches simulator containers to reach N robots, lets the fleet
settle, then measures for a window:

  * command latency p50/p95/p99 — timing repeated `POST /fleet/command`, computed
    from the raw sample array (never pre-bucketed averages; STYLE.md);
  * MQTT broker CPU % — `docker stats` on the mosquitto container;
  * telemetry + InfluxDB throughput (points/s) — Prometheus counter deltas, the
    same counters the Grafana dashboard reads.

It writes a CSV to loadtest/runs/ and prints the markdown table to paste into
ADR-0005. This needs Docker and the running stack; it is not a unit test.

Usage: `just loadtest` or `python loadtest/ramp.py --max 20`.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx2

FLEET_URL = os.getenv("FLEET_URL", "http://localhost:8005")
PROM_URL = os.getenv("PROM_URL", "http://localhost:9090")
NETWORK = os.getenv("COMPOSE_NETWORK", "twin-fleet_default")
SIM_IMAGE = os.getenv("SIM_IMAGE", "twin-fleet-simulator")
BROKER_CONTAINER = os.getenv("BROKER_CONTAINER", "twin-fleet-mosquitto-1")

DEFAULT_STEPS = (1, 2, 5, 10, 15, 20)
SETTLE_S = 4.0  # let the fleet register and telemetry stabilise before measuring
WINDOW_S = 10.0  # measurement window per step
CMD_INTERVAL_S = 0.2  # gap between coordinated commands while measuring


def percentile(samples: list[float], q: float) -> float:
    """Linear-interpolated percentile of raw samples (q in 0..100)."""
    if not samples:
        return float("nan")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (q / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass(frozen=True)
class StepResult:
    n: int
    telem_points_s: float
    influx_points_s: float
    cmd_p50_ms: float
    cmd_p95_ms: float
    cmd_p99_ms: float
    broker_cpu_pct: float


# ─── stack probes ────────────────────────────────────────────────────────────


def _prom_scalar(client: httpx2.Client, query: str) -> float:
    resp = client.get(f"{PROM_URL}/api/v1/query", params={"query": query})
    resp.raise_for_status()
    result = resp.json()["data"]["result"]
    return float(result[0]["value"][1]) if result else 0.0


def fleet_size(client: httpx2.Client) -> int:
    try:
        return int(client.get(f"{FLEET_URL}/fleet").json()["size"])
    except (httpx2.HTTPError, KeyError, ValueError):
        return -1


def broker_cpu_pct() -> float:
    """Mosquitto container CPU %, via docker stats (one-shot)."""
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", BROKER_CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(out.stdout.strip().rstrip("%"))
    except ValueError:
        return float("nan")


def launch_robot(i: int) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            f"twin-fleet-robot-{i}",
            "--network",
            NETWORK,
            "-e",
            "MQTT_HOST=mosquitto",
            "-e",
            f"ROBOT_ID=robot_{i}",
            "-e",
            "TELEMETRY_HZ=50",
            SIM_IMAGE,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def kill_robot(i: int) -> None:
    subprocess.run(
        ["docker", "kill", f"twin-fleet-robot-{i}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ─── measurement ─────────────────────────────────────────────────────────────


def measure_command_latency(client: httpx2.Client, window_s: float) -> list[float]:
    """Fire coordinated commands for window_s; return per-command latencies (ms)."""
    samples: list[float] = []
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        start = time.perf_counter()
        # A failed dispatch still counts as latency observed by the caller.
        with contextlib.suppress(httpx2.HTTPError):
            client.post(f"{FLEET_URL}/fleet/command", json={"kind": "home"})
        samples.append((time.perf_counter() - start) * 1000.0)
        time.sleep(CMD_INTERVAL_S)
    return samples


def measure_step(client: httpx2.Client, n: int) -> StepResult:
    telem_before = _prom_scalar(client, "sum(twin_telemetry_messages_total)")
    influx_before = _prom_scalar(client, "sum(twin_influx_points_written_total)")
    t0 = time.monotonic()

    latencies = measure_command_latency(client, WINDOW_S)
    cpu = broker_cpu_pct()

    elapsed = time.monotonic() - t0
    telem_after = _prom_scalar(client, "sum(twin_telemetry_messages_total)")
    influx_after = _prom_scalar(client, "sum(twin_influx_points_written_total)")
    return StepResult(
        n=n,
        telem_points_s=(telem_after - telem_before) / elapsed,
        influx_points_s=(influx_after - influx_before) / elapsed,
        cmd_p50_ms=percentile(latencies, 50),
        cmd_p95_ms=percentile(latencies, 95),
        cmd_p99_ms=percentile(latencies, 99),
        broker_cpu_pct=cpu,
    )


# ─── ramp ────────────────────────────────────────────────────────────────────


def steps_up_to(max_n: int) -> list[int]:
    steps = [s for s in DEFAULT_STEPS if s <= max_n]
    if max_n not in steps:
        steps.append(max_n)
    return steps


def ensure_robots(client: httpx2.Client, running: int, target: int) -> None:
    for i in range(running + 1, target + 1):
        launch_robot(i)
    deadline = time.monotonic() + SETTLE_S + target * 0.2
    while time.monotonic() < deadline:
        if fleet_size(client) >= target:
            break
        time.sleep(0.5)
    time.sleep(SETTLE_S)  # let telemetry rates stabilise


def run_ramp(max_n: int) -> list[StepResult]:
    results: list[StepResult] = []
    running = 0
    with httpx2.Client(timeout=httpx2.Timeout(10.0)) as client:
        try:
            for n in steps_up_to(max_n):
                print(f"[ramp] scaling to {n} robots ...")
                ensure_robots(client, running, n)
                running = n
                result = measure_step(client, n)
                results.append(result)
                print(
                    f"[ramp] N={n:<3} cmd p50={result.cmd_p50_ms:.1f}ms "
                    f"p95={result.cmd_p95_ms:.1f}ms p99={result.cmd_p99_ms:.1f}ms "
                    f"broker_cpu={result.broker_cpu_pct:.0f}% influx={result.influx_points_s:.0f}/s"
                )
        finally:
            for i in range(1, running + 1):
                kill_robot(i)
    return results


# ─── output ──────────────────────────────────────────────────────────────────


def write_csv(results: list[StepResult]) -> Path:
    runs = Path(__file__).parent / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"ramp-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=[f.name for f in StepResult.__dataclass_fields__.values()]
        )
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))
    return path


def markdown_table(results: list[StepResult]) -> str:
    header = (
        "| N robots | telem pts/s | cmd p50 | cmd p95 | cmd p99 | broker CPU % | Influx pts/s |\n"
        "| -------- | ----------- | ------- | ------- | ------- | ------------ | ------------ |"
    )
    rows = [
        f"| {r.n} | {r.telem_points_s:.0f} | {r.cmd_p50_ms:.1f} ms | {r.cmd_p95_ms:.1f} ms "
        f"| {r.cmd_p99_ms:.1f} ms | {r.broker_cpu_pct:.0f} | {r.influx_points_s:.0f} |"
        for r in results
    ]
    return "\n".join([header, *rows])


def main() -> None:
    parser = argparse.ArgumentParser(description="twin-fleet 1->N load ramp (ADR-0005).")
    parser.add_argument("--max", type=int, default=20, help="Maximum robot count.")
    args = parser.parse_args()

    results = run_ramp(args.max)
    csv_path = write_csv(results)
    print("\n" + markdown_table(results))
    print(f"\n[ramp] raw run written to {csv_path}")


if __name__ == "__main__":
    main()
