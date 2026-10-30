# A4 power-path layout analysis (trial 4, before curvature planning)

The 12 AWG conductors retain a 16 mm minimum radius. The 14 AWG HAT
branch retains 13.2 mm. No ampacity, bend or clearance limit is changed.
The ESC inline XT60 is removed: power_y.P2 now solders directly to esc.J_PWR.
This consolidates the former esc_power harness into power_y (11 installed
harnesses instead of 12). The existing two motor harnesses remain separate.

Trial 1 shifted the complete steering module forward 40 mm, straightened
the battery, and tested a vertical XT90. It left 7.5 mm clearance to a
swept tie rod (8 mm required), so that connector position was rejected.
Trial 2 tested a transverse XT90 but automatic placement left too little
run for the battery bend. Trial 3 moved the front steering module another
40 mm and used a longitudinal XT90; its first location left only 13.8 mm
between battery and connector exits. Trial 4 gives 49.8 mm longitudinal
run by placing the XT90 farther forward, offset to the left of the servo.
The wheelbase change is a design trial, not a validated final layout.

Resolved endpoints from the fast unplanned run (millimetres, robot frame):

| Conductor | From XYZ | To XYZ | Straight distance | Intended bends |
|---|---|---|---:|---|
| W_Y_P_FUSE | [345.0, 19.5, 40.0] | [275.0, 50.0, 39.0] | 76.363 | S turn along open front bay, 16 mm radius |
| W_Y_P_IN | [320.2, 18.5, 43.0] | [345.0, 19.5, 40.0] | 25.001 | Near straight; align splice with pin axis |
| W_Y_N_IN | [320.2, 29.5, 43.0] | [345.0, 30.5, 40.0] | 25.001 | Near straight; align splice with pin axis |
| W_Y_P_ESC | [345.0, 19.5, 40.0] | [146.0, 41.0, 39.6] | 200.158 | S turn along open front bay, 16 mm radius |
| W_Y_N_ESC | [345.0, 30.5, 40.0] | [146.0, 37.0, 39.6] | 199.107 | S turn along open front bay, 16 mm radius |
| W_Y_P_HAT | [245.0, 50.0, 39.0] | [116.5, -50.4, 105.276] | 176.025 | Two vertical/horizontal turns, at least 13.2 mm radius |
| W_Y_N_HAT | [345.0, 30.5, 40.0] | [121.5, -50.4, 105.276] | 246.491 | Two vertical/horizontal turns, at least 13.2 mm radius |
| W_BAT_P | [210.0, 8.0, 40.0] | [259.8, 18.5, 43.0] | 50.983 | Gentle S, 16 mm radius; 49.8 mm longitudinal run |
| W_BAT_N | [210.0, 12.0, 40.0] | [259.8, 29.5, 43.0] | 52.871 | Gentle S, 16 mm radius; 49.8 mm longitudinal run |

The available battery S-run is 49.8 mm in X with at most 17.5 mm lateral
offset. A two-arc S has R = (X² + Y²)/(4Y) = 39.8 mm for that worst
offset before allowing straight terminal runs. A 16 mm S needs 28.5 mm
in X for that offset, leaving 21.3 mm for straight ends. The positive and
negative pins are routed independently; they are not converged into a
shared core. The input splices should be aligned with the XT90 pin axes
so the 24.8 mm trunks remain straight.

This analysis is necessary but not sufficient: trial 4's unplanned routes
still report a 3.8 mm wheel clearance and conductor/connector collisions.
Curvature planning, supports, the complete occupancy solve and dynamic
checks must all pass. The official DS3218 import is being reconciled
before choosing and running the next planned subset.

## Trial 5: room for the return bends

The battery-only planned subset now passes: battery_lead valid, no bend or
conductor-intersection errors. Its paired input trunks still doubled back over
the splice sleeves (3.756 and 3.388 mm penetration). They are now straightened
by putting SP/SN at the XT90 pin heights and giving them 29.8 mm in X.

The complete steering module moves another 80 mm forward (420 mm wheelbase).
Its earliest stationary supplied-servo surface is now X=389.795 mm. A 16 mm
semicircular return from each splice occupies X=350..366 mm: the power bay has
room before the servo for that turn. The frame stays 80 mm wide in the front
bay, with a small X=240..280, Y=40..65 mm tab under the fuse. Estimated added
plate material is 60.0 g; the preceding model reported 117.236 g mass margin.
The final CAD mass and swept-wheel clearances still require verification.

Updated power paths (all mm; unchanged endpoints retained from trial 4):

| Conductor | From XYZ | To XYZ | Straight distance | Bend construction |
|---|---|---|---:|---|
| W_Y_P_IN | [320.2, 18.5, 43] | [350, 18.5, 43] | 29.800 | Straight |
| W_Y_N_IN | [320.2, 29.5, 43] | [350, 29.5, 43] | 29.800 | Straight |
| W_Y_P_ESC | [350, 18.5, 43] | [146, 41, 39.6] | 205.265 | 16 mm return arc, then rearward run beside battery |
| W_Y_N_ESC | [350, 29.5, 43] | [146, 37, 39.6] | 204.166 | Separated 16 mm return arc, then rearward run |
| W_Y_P_FUSE | [350, 18.5, 43] | [275, 50, 39] | 81.445 | 16 mm return arc, then near-straight fuse approach |
| W_Y_P_HAT | [245, 50, 39] | [116.5, -50.4, 105.276] | 176.025 | Two turns at least 13.2 mm; climb outside deck |
| W_Y_N_HAT | [350, 29.5, 43] | [121.5, -50.4, 105.276] | 249.949 | Separated return and deck climb, at least 13.2 mm |
| W_BAT_P | [210, 8, 40] | [259.8, 18.5, 43] | 50.983 | Previously verified 16 mm S bend |
| W_BAT_N | [210, 12, 40] | [259.8, 29.5, 43] | 52.871 | Previously verified 16 mm S bend |

## Trial 6: retained support posts and installed splice axes

Trial 5 verified battery_lead and both input trunks; power_y retained six errors:
negative ESC clearance 0.362 mm (1.2 mm required), input/ESC tangent radii
14.256/12.122 mm (16 mm required), positive HAT infeasible curvature, positive
ESC unsupported span 211.492 mm (99.469 mm maximum), and positive HAT terminal
load 103.234 N (20 N maximum). Assembly/model/connectors had zero errors and
model mass was 2.144314 kg, leaving 55.686 g below the 2.2 kg limit.

All terminals and straight distances in trial 5 remain unchanged. SP and SN now
have explicit X sleeve axes, preserving the 29.8 mm straight input runs instead
of allowing the branch fit to tilt their tangents. Positive ESC runs over the
fuse and connector at Z=62 mm through [300,40.75,62] and [230,49.75,62]. Two
bolted, collared support posts at [300,35,28] and [230,44,28] retain those runs;
the 70.576 mm distance between supports is below the preceding 99.469 mm span
limit. Their M3 screws have retained caps. The fuse tab grows 16 mm rearward to
support the second post. Vertical space between Z=43 and 62 provides the rise
for the first 16 mm turn; the front return still has X=350..366 mm available.

Negative ESC instead passes [330,5,40], [235,5,40], [215,29,40], inside the
XT90 footprint and around the battery's X=210 end. Positive HAT uses
[160,-85,116] and [116.5,-100,105.276] to leave a straight 49.6 mm final
approach toward its terminal and room for a 13.2 mm bend before that approach.
These are routing guides; no support, clearance, curvature or force check is
waived. The complete planned subset must verify the construction.
