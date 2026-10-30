# R3 virtual hardware protocol (`r3-hal/1`)

Robot software talks to hardware only through `r3_hal`. On a real Raspberry Pi
the `linux` backend uses `/dev/i2c-*`, `/sys/class/pwm`, `/dev/gpiochip*`, and
`/dev/tty*`. In simulation the `virtual` backend connects to the digital twin
over TCP and speaks newline-delimited JSON. The same program binary runs in both.

Selection: `R3_HAL=linux|virtual` (default `linux`), `R3_HAL_ENDPOINT=tcp://host:port`.

## Time model: lockstep

Simulation time advances **only** while the software is blocked in
`clock.sleep`/`clock.sleep_until`, `uart.read` with a timeout,
`gpio.wait_edge`, or `camera.capture`. Every other request is answered at the current simulation
instant (software compute time is treated as zero; a CPU-time model may be
added later). This makes runs deterministic for a given seed.

## Messages

Request: `{"id": <int>, "op": "<name>", ...args}`. Response:
`{"id": <int>, "ok": true, "t": <sim seconds>, ...result}` or
`{"id": <int>, "ok": false, "t": ..., "error": "<code>", "message": "..."}`.
Binary payloads are lists of byte integers, except camera frames, which are
base64 strings (`"encoding": "base64"`).

| op | args | result |
| --- | --- | --- |
| `hello` | `protocol: "r3-hal/1"`, `module` | `computer`, `bindings` (name -> interface params from `robot.yaml`) |
| `clock.now` | – | `t` |
| `clock.sleep_until` | `t` | returns when sim time >= t |
| `gpio.configure` | `chip`, `line`, `direction` (`in`/`out`), `bias`, `edge` (`none`/`rising`/`falling`/`both`) | – |
| `gpio.read` | `chip`, `line` | `value` (0/1) |
| `gpio.write` | `chip`, `line`, `value` | – |
| `gpio.wait_edge` | `chip`, `line`, `timeout` | `edges`: list of `{t, rising}` since last call (may block until timeout) |
| `gpio.count_edges` | `chip`, `line` | `count`, `last_t` (non-blocking edge counter, rising edges) |
| `pwm.configure` | `chip`, `channel`, `period_ns`, `duty_ns`, `enabled` | – |
| `i2c.transfer` | `bus` (e.g. `/dev/i2c-1`), `addr`, `write`, `read_len` | `data`; error `i2c_nack` when no device acks |
| `spi.transfer` | `bus`, `cs`, `data`, `speed_hz`, `mode` | `data` |
| `uart.configure` | `device`, `baud` | – |
| `uart.write` | `device`, `data` | `written` |
| `uart.read` | `device`, `max`, `timeout` | `data` (blocks up to timeout for >=1 byte) |
| `camera.configure` | `port` (`cam0`/`cam1`, the `csi` binding `device`), `width`, `height`, `fps`, `format` (`rgb888`/`gray8`) | `width`, `height`, `fps`, `format`, `intrinsics` (`fx`, `fy`, `cx`, `cy` px, `hfov_deg`, `vfov_deg`, `distortion`) |
| `camera.capture` | `port`, `timeout` | `frame_t` (exposure start), `sequence`, `width`, `height`, `format`, `exposure_s`, `encoding: base64`, `data` (row-major, top row first); blocks until a frame completes |
| `camera.close` | `port` | – |
| `device.request` | `device` (`simulation.devices` key), `operation`, `payload` (object), `timeout` | `result` object; waits in lockstep for project device data |
| `log` | `level`, `message`, `fields` | – (stored in the run recording) |
| `bye` | – | twin finishes the scenario |

### Cameras

A `csi` binding in `robot.yaml` (`device: cam0`) must reach a camera
component through the cable and connectors. The camera's catalog behavior
`camera.*` gives the field of view, sensor modes and lens pose. The twin
renders frames with MuJoCo's offscreen renderer (EGL/OSMesa headless, CGL on
macOS) from a camera at the compiled lens pose, and adds Gaussian read noise.
The frame timing works like libcamera:

- Frame `k` is exposed at `t_k = t_configure + startup + k / fps`.
- It completes one frame period later, plus the ISP latency.
- `capture` returns the newest completed frame that has not been returned
  yet. Older frames are dropped, which shows as gaps in `sequence`.

Rendering reads a private copy of the state at `t_k`, so cameras never change
the physics. On the Pi the `linux` backend uses picamera2 (libcamera) with a
fixed `FrameDurationLimits`.

Errors: `mode_unsupported` (resolution or fps beyond the sensor modes),
`bad_format`, `no_such_device` (no wired camera on the port), `not_configured`,
`timeout`.

Errors are typed (`i2c_nack`, `no_such_device`, `pin_not_bound`,
`electrically_unpowered`, `bus_contention`, `brownout`) so software and agents
can react. Example: if the Pi's 5 V rail browns out in the electrical model the
twin answers every request with `brownout` and the program is restarted after
the modeled boot time.
