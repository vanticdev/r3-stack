# Simulation (`r3_sim`)

Digital-twin co-simulation kernel for R3 robots. The real robot software runs as
a separate process against virtual hardware, in lockstep with the physics.

| Module | Role |
| --- | --- |
| `vehicle/mjcf.py` | MJCF from `robot.yaml`: joint-child bodies, fixed parts lumped with composite inertia, CAD mass properties when `build/cad/cad_model.json` exists (`cad_mass_estimated` otherwise), cylinder tire contact |
| `vehicle/dynamics.py` | open differential (tendon, reflected rotor inertia), Ackermann tie-rod linkage, rolling resistance moment, aerodynamic drag |
| `vehicle/servo.py` | PWM servo: deadband, voltage-scaled stall torque and no-load rate, horn load by virtual work |
| `kernel.py` | 1 ms fixed-step loop in the documented order; seeded RNG streams per subsystem |
| `radio.py`, `crsf.py` | link budget, ELRS FHSS hop frequency, Gudmundson shadowing, LoRa PER, ELRS receiver emitting CRSF 0x16/0x14 frames (LQ, RSSI, SNR) |
| `radio_scene.py` | RF scene from the spec: ITU ground, obstacle boxes, car body per material (CAD meshes or catalog primitives), antennas, CAD `rf.antenna_position` |
| `radio_sionna.py` | **Sionna RT** ray tracing (CPU, Dr.Jit LLVM): position-cached traces with the posed car body, float64 path lengths, coherent multipath at the hop frequency |
| `radio_analytic.py` | **labeled fallback** two-ray + knife-edge model (diagnostic `radio_propagation_fallback`); closed-form dipole two-ray reference for validation |
| `radio_plots.py` | `coverage_map.png`: Sionna radio map around the course + coherent path-solver RSSI along the route |
| `devices/` | MPU-6050, INA226 (register level), u-blox NMEA GPS (byte timing), Hall encoder edges |
| `signals.py` | binding → device reachability from the net graph (an unwired device sees nothing) |
| `hal_server.py` | `r3-hal/1` server (see `packages/hardware-interface/PROTOCOL.md`), brownout errors |
| `scenario.py`, `evaluation.py` | software subprocess, brownout reboot, watchdog restarts, `expect` evaluation, artifacts |
| `fallbacks.py` | **labeled fallbacks**, used only when `r3_sim.electrical` / `r3_wiring` are missing |
| `stage.py` | `run(project) -> StageResult` over all scenarios |

Outputs per scenario under `build/sim/<scenario>/`: `summary.json`, `recording.npz`,
`trajectory.png`, `speed.png`, `battery.png`, `rssi_vs_distance.png`, `coverage_map.png`, `currents.png`,
`renders/*.png`, `renders/clip.mp4`, `vehicle.xml`, `software.log`. `build/sim/overview.png`
summarizes all scenarios.

`build/sim/radio/` holds the Sionna scene (`scene_<digest>/scene.xml` + PLY meshes, loadable with
`sionna.rt.load_scene`) and cached coverage grids.

## RF propagation: Sionna RT

The radio channel between the pilot handset and the car antenna is ray-traced with
[Sionna RT](https://nvlabs.github.io/sionna/rt/) (NVIDIA, Apache-2.0) on Mitsuba 3 / Dr.Jit.

**Install** (shared venv): `uv pip install --python /Users/godbrigero/Documents/r3-stack/.venv/bin/python sionna-rt`
(tested: sionna-rt 2.2.0, mitsuba 3.9.1, drjit 1.5.0).

**Runtime requirements (headless CPU):**

* Mitsuba variant `llvm_ad_mono_polarized` is selected before Sionna is imported. Sionna's own default prefers
  CUDA, then Metal. Override it with `R3_SIONNA_VARIANT=cuda_ad_mono_polarized` on a GPU worker.
* The Dr.Jit LLVM backend needs a shared **libLLVM** (LLVM ≥ 11) at runtime. macOS: `brew install llvm`
  (found in `/opt/homebrew/opt/llvm/lib/libLLVM.dylib`). Debian/Ubuntu (R3 worker image): `apt-get install libllvm15`
  or newer. If the library is not on the default search path, set `DRJIT_LIBLLVM_PATH=/path/to/libLLVM.so`.
* No GPU, display, OpenGL or browser is needed. Kernels are JIT-compiled on first use (about 1 s) and cached by Dr.Jit.
* If Sionna, Mitsuba or libLLVM is missing, the twin falls back to the analytic model in `radio_analytic.py` and
  emits the warning diagnostic `radio_propagation_fallback`. `R3_RADIO_BACKEND=analytic` forces that fallback
  (for comparisons).

**Model.** The scene comes from the spec:
* ground: an ITU-R P.2040 `medium_dry_ground` plane, or `radio_environment.ground_material`;
* obstacle boxes, with ITU materials or a labeled plastic estimate;
* the car body: chassis plate outline, CAD meshes or catalog primitives, merged per material (without the
  receiver and the antenna itself);
* half-wave dipoles, V-polarized, scaled to the catalog peak gains;
* the car antenna at CAD `rf.antenna_position`, else the center of the antenna component's catalog `dipole`
  solid, else the receiver pose + `antenna_height_above_mount`.

`PathSolver` runs specular reflection to depth 3 with refraction and UTD wedge diffraction. Traces are cached on a
0.25 m × 15° heading grid (20 traces/s at 5 m/s), with the car body posed for each trace. Between traces each path's
last segment is recomputed exactly. The narrowband channel H(f) = Σ aᵢ e^(−j2πf Lᵢ/c) is evaluated per packet at
the ELRS FHSS hop frequency, so small-scale fading *is* the ray-traced multipath. No statistical fading is added.
Path lengths are recomputed in float64 from the path vertices: Sionna's float32 delays lose the
direct-vs-ground phase beyond a few hundred metres (0.8 dB error at 1 km). Gudmundson log-normal shadowing
(`shadowing_sigma_db`) remains for clutter that is not in the scene.

**Validation** (`tests/test_sim_radio_sionna.py`): on flat ground without obstacles, Sionna RT matches the closed-form
two-ray field sum with the same ITU material and dipole patterns within 0.02 dB from 3 m to 1500 m. The test bound is 1 dB.

**Known limits.** The car body is modelled as exact *blockage* only. The antenna sits about 0.65 λ above the Pi/HAT
stack. That is near field, where ray optics and UTD do not hold. A reciprocal trace from the antenna that samples
the body edges did not converge: it varied by ±8 dB at 300 m between 0.4 M and 2 M rays. The right input
would be an installed-antenna pattern from a full-wave solver (for example openEMS), loaded as a Sionna antenna pattern.
Diffuse scattering (ground roughness) is off.

Simulation success does not imply hardware success; every summary lists its assumptions.
