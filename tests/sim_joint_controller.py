"""Generic PWM bench controller for end-to-end HAL/physics tests."""
from r3_hal import open_hal

hal = open_hal('joint_drive_bench')
binding = hal.bindings()['esc_pwm']
esc = hal.pwm(binding['device'], binding['channel'])
hal.log('info', 'bench controller started', event='start')
t = hal.clock.now()
while True:
    duty = max(0, min(1, (t - .05) / 2.5)) if t < 7 else 0
    esc.set_pulse_us(1500 + 500 * duty)
    t += .01
    hal.clock.sleep_until(t)
