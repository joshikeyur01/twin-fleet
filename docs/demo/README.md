# Demo assets

The README references `twin-fleet.gif`. Record it on a machine with a display:

```bash
just up           # infra + 5 services
just fleet 20     # 20 synthetic robots
# then, with the viz (http://localhost:8004) or Grafana (http://localhost:3000) up:
just record       # peek → docs/demo/twin-fleet.gif  (needs `peek` + a display)
```

Good things to capture in the 20 s clip:

- **The viz** (`:8004`) rendering the fleet as a grid of UR5 arms — the status
  pill reads `live · N robots`. Verified rendering live at N=12 during the build.
- **The ramp bending** — run `just loadtest` and watch Grafana's command-latency
  panel and the per-robot liveness timeline while robots climb 1 → 20.
- **The kill demo** — `just kill-robot 7`, and the robot drops from `GET /fleet`
  and its Grafana liveness cell goes red within the liveness window (~1 s).

The animated GIF is not committed here because it requires a display to record;
everything it would show has been verified live (see `WHAT_I_LEARNED.md` and the
ADR-0005 ramp tables under `loadtest/runs/`).
