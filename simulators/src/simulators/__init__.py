"""Synthetic UR5 simulators — the disclosed Gazebo stand-in (no ROS on this host).

One process per robot_id (`just fleet N=` launches N containers). Each publishes
UR5-shaped JointTelemetry to its own namespaced topics at ~50 Hz, sets an MQTT
Last-Will so its death is seen instantly (ADR-0003), and reacts to coordinated
commands (home / move_joints) so the fleet demo actually moves. The physics is a
deterministic sine model; the bytes the broker and services see are identical to
a real feed, which is all the scaling proof needs (VISION)."""
