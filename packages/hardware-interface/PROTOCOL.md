# R3 virtual hardware protocol (`r3-hal/1`)

Robot software talks to hardware only through `r3_hal`. On a real Raspberry Pi
the `linux` backend uses `/dev/i2c-*`, `/sys/class/pwm`, `/dev/gpiochip*`, and
`/dev/tty*`. In simulation the `virtual` backend connects to the digital twin
over TCP and speaks newline-delimited JSON. The same program binary runs in both.

Selection: `R3_HAL=linux|virtual` (default `linux`), `R3_HAL_ENDPOINT=tcp://host:port`.

## Time model: lockstep

Simulation time advances **only** while the software is blocked in
`clock.sleep`/`clock.sleep_until`, `uart.read` with a timeout, or
`gpio.wait_edge`. Every other request is answered at the current simulation
instant (software compute time is treated as zero; a CPU-time model may be
added later). This makes runs deterministic for a given seed.

## Messages

Request: `{"id": <int>, "op": "<name>", ...args}`. Response:
`{"id": <int>, "ok": true, "t": <sim seconds>, ...result}` or
`{"id": <int>, "ok": false, "t": ..., "error": "<code>", "message": "..."}`.
Binary payloads are lists of byte integers.

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
| `log` | `level`, `message`, `fields` | – (stored in the run recording) |
| `bye` | – | twin finishes the scenario |

Errors are typed (`i2c_nack`, `no_such_device`, `pin_not_bound`,
`electrically_unpowered`, `bus_contention`, `brownout`) so software and agents
can react. Example: if the Pi's 5 V rail browns out in the electrical model the
twin answers every request with `brownout` and the program is restarted after
the modeled boot time.
