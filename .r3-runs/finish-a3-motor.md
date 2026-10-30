# A3 motor lead layout

The two motor leads now run separately from the ESC's original pigtail centres,
clocked apart, to soldered motor tabs. The upper lead leaves the ESC upward and
outward; the lower lead leaves toward the lower motor terminal. The existing
combined J_MOT catalog interface remains available to other projects.

The leads use 18 AWG silicone wire. Individual routing removes the artificial
shared centreline fork that overlapped by 1.726 mm in the best bundled trial.
Neither the motor can nor the purchased ESC body was changed. The motor lead
post and its capped mounting hole move through the chassis interface to
(80, 60, 28) mm, clearing the separate paths.

Validation on the isolated candidate:
- Offline recomposition, assembly and model: zero errors.
- Both `esc_motor` and `esc_motor_upper`: valid, zero wiring errors in 58.11 s.
- Routed lengths approximately 42.4 and 65.1 mm; connector restraint and span
  checks pass without an intermediate tie. Full assembly sag still needs the
  final real-firmware steering sweep.
- Electrical stage: zero errors, using these actual motor routes and the last
  full assembly's other wire lengths. The 58.8106 A, two-second stall reaches
  45.2341 C. Installed continuous ampacity is 6.9282 A; continuous operating
  points pass. Burst-over-ampacity remains an explicit warning, not an exemption.

This isolated result does not release the full harness package. The complete
route build, connector check and steering sweep must be regenerated together.
