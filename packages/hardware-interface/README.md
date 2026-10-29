# Hardware interface

Reserved for a narrow software-facing hardware API and its eventual virtual and
physical adapters. Candidate operations include GPIO, PWM, I²C, SPI, UART, and
sensor access; the initial supported subset remains undecided.

Documentation only. Docker provides a userspace execution environment, not CPU
or peripheral emulation. Universal board or Linux device-file compatibility is
not assumed. The existing `watchdog/` continues to own target process management.

See [repository layout](../../docs/architecture/repository-layout.md).
