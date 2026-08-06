"""command-svc: accepts REST commands for one robot, validates against
contracts, publishes namespaced MQTT setpoints.

Single-robot and fleet-unaware by design (AGENTS.md): it publishes to
`twin/<robot_id>/ur5/cmd/joints` for the robot named in the request and knows
nothing about the fleet. fleet-svc fans a coordinated command out across it —
this service never grows fleet awareness."""
