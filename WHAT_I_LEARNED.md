# What I learned

The honest list. Written while building and load-testing `twin-fleet`, not
polished after the fact.

## The scaling breakpoint was code, and it was hiding at N=1

I expected the ceiling to be the broker or the database at some N up near 20. It
was neither. `telemetry-svc` did `await write_api.write(record=point)` **per
message** — one HTTP round-trip to InfluxDB each — so ingest serialised at
~700 points/s *no matter how many robots fed it*. A single robot produces ~900
points/s, so the service was already behind at **one** robot; adding robots just
dropped more QoS-0 telemetry (throughput drifted *down*, 700 → 509, as N rose).
Batching (flush 500 points or every 0.5 s) lifted it ~25× to **17,978 pts/s at
N=20** — the full produced rate, linear in N. Broker CPU never passed ~46 %. The
lesson: measure before you architect. The "obvious" fix (Kubernetes, a second
broker) would have bought headroom the numbers said I already had; the real fix
was one `await` in a loop. That is the entire point of the load test.

## A documented command that never worked: `just fleet N=20`

Every doc said `just fleet N=20`. Running it revealed that is **invalid just
syntax** — just reads `N=20` after a recipe as either another recipe or a global
assignment, not a positional argument, so `{{N}}` became the literal string
`N=20` and `seq 1 N=20` failed. The recipe is positional; the correct form is
`just fleet 20`. I had written and re-written that wrong invocation into README,
ROADMAP, VISION, and AGENTS without once running it. Docs that describe a command
are worthless until the command has actually been typed.

## `httpx2` is not a typo

A note claimed the upstream `httpx2` dependency was a typo for `httpx`. I
"fixed" it — and then a Starlette deprecation warning made me check PyPI:
`httpx2` is a real, current package (the next-generation HTTP client that
Starlette 1.x's TestClient now *requires*; plain `httpx` is deprecated there). My
fix was the regression. Trust the registry over a stale memory; a two-second
`curl pypi.org` beats confidently "correcting" a dependency.

## Fire-and-forget means a dead robot can't be a "failed command"

I wrote ADR-0004 claiming a killed robot shows up as a *failed entry* in a
coordinated command. Implementing the fan-out proved that dishonest:
`command-svc` publishes a setpoint and returns 202 whether or not anything is
listening — it *cannot* observe a dead robot. So a killed robot simply leaves the
registry (`GET /fleet` shrinks), and a "failed entry" is only ever a genuine
dispatch failure (command-svc/broker down → 503/timeout, never a fake 200). Two
honest degradation modes, not one hand-wave. The design forced the doc to be
truthful.

## No ROS, on purpose — and it made the measurement cleaner

There is no ROS 2 / Gazebo here, so the `twin-services` DDS↔MQTT bridge was
dropped and N synthetic simulators publish namespaced MQTT directly. This felt
like a compromise until the load test: Gazebo × 20 on a laptop is not viable, and
even if it ran it would have measured the *physics engine* choking, not the
architecture. Synthetic simulators isolate exactly the variable ADR-0005 exists
to find. The disclosed stand-in was the right instrument, not a shortcut.

## Per-container robots, not `docker compose --scale`

`--scale` can't hand each replica a unique human-readable `robot_id`, and the
kill-one-robot demo needs to `docker kill` a *single* robot. So `just fleet N`
launches N individually-named `twin-fleet-robot-<i>` containers (a templated
launcher). Passive discovery + MQTT Last-Will then does the rest: kill a
container, its will fires "offline", and it drops from `GET /fleet` in ~1 s.

## Every twin-* stack fights for the same host ports

`twin-services`, `twin-anomaly`, and `twin-fleet` all publish
1883/8086/3000/9090/8001–8005/50051. Only one can run at a time. Worse: the
integration test's `_stack_is_up()` probed `/healthz/live` on those ports and was
happily fooled by `twin-anomaly` answering on 8001–8005 — it ran the fleet tests
against the wrong stack. Fix: probe a *twin-fleet-specific* endpoint (`GET
/fleet`, which only `fleet-svc` serves) so the tests can tell whose stack is up.

## Generated stubs must be regenerated with the workspace toolchain

I first generated the gRPC stubs in a throwaway `uv` environment to move fast.
That risks a protoc/grpcio version drift from the workspace, which would trip
CI's stub-freshness gate. Regenerating once with the synced workspace's
`grpc_tools` (what CI uses) is the version that gets committed.

## Inherited traps that still bite

- **iCloud hides `.venv`**: `chflags -R nohidden .venv` before every `uv run`
  recipe, or Python 3.12 skips the `.pth` and editable imports vanish silently.
- **Docker `docker kill` and `restart` policy**: simulators run with `--rm` and
  no restart policy, so a killed robot *stays* dead — that is the demo, not a bug.
