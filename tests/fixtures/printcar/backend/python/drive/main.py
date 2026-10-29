"""Open-loop drive test: fixed ESC pulse for run_s seconds, then neutral (r3_hal only)."""
from r3_hal import open_hal

hal = open_hal("drive")
binding, config, clock = hal.bindings()["esc_pwm"], hal.config(), hal.clock
esc = hal.pwm(binding["device"], int(binding["channel"]))
start = clock.now()
while True:
    t = clock.now() - start
    esc.set_pulse_us(float(config.get("throttle_us", 1600)) if t < float(config.get("run_s", 3.0)) else 1500.0)
    clock.sleep_until(clock.now() + 0.02)
