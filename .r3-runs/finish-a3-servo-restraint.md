# Servo lead restraint

The servo-post B shelf is now 50 x 54 x 2 mm, giving the pigtail a wider forward
turn. The power post, south deck post and approach post carry the lead above the
receiver to the south-facing HAT header. Two additional collared PETG posts
support the climb and upper span. Their poses come from deck interfaces; both
use M3x10 screw joins and underside caps. The deck tabs carry those mounting
holes. No routing, bend, span or clearance limit changed.

The latest isolated validation has **0 errors, valid=true**, in 816.94 seconds.
Eight real supports were installed. The worst shaker clearance is **12.3891 mm**
for W_SRV_SIG against wheel_fr at 1.753 s, versus the unchanged **8 mm** limit.
This includes the 0.5 g vibration, 3 g bump and lock-to-lock cable drive. It is
not the full real-firmware steering scenario or the combined harness release.

Required support centres (mm): (325,-10,41.87), (185.21,-49.21,62),
(135,-79.347,113), (85.5,-100,128), (159.6546,-67.412733,84.586),
(117.4532,-104.542333,135.4386). The final two eliminate the previous 81.65 mm
and 81.10 mm unsupported spans against the 77.84 mm limit. The route rises to
85 mm over the extended deck tab and passes clear of the upper post collar.

After promotion, offline CAD recomposition, assembly and model each report
**0 errors**. New geometry needing the final Onshape rebuild:
servo-post-al6061-cable, upper-deck-petg, servo-climb-post-petg and
servo-upper-post-petg. Earlier restraint changes also include
power-lead-post-petg, servo-south-post-petg and servo-approach-post-petg.
