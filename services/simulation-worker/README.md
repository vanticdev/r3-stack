# Simulation worker (`r3_sim_worker`)

Runs digital-twin scenario jobs for a project and records the input fingerprints
(`robot.yaml` hash, git commit, lock revisions) in every result.

```
python -m r3_sim_worker examples/robots/rc-car                       # project firmware, all scenarios
python -m r3_sim_worker examples/robots/rc-car --scenario full_throttle \
    --software-cmd python tests/sim_test_controller.py               # built-in test controller
```

The exit status is 0 only when every selected scenario passes. Shared simulation
behavior lives in `packages/simulation/` (`r3_sim`).
