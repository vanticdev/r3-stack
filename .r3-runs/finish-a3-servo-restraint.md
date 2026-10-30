# Servo lead restraint

The servo-post B shelf supports the pigtail's forward U-turn. A 2 mm radial collar on the power post holds the climb away from the post surface; the south deck post and a capped approach post carry the lead above and around the receiver to the HAT's south-facing header. The south post has a separate collar for the IMU lead. All supports are joined to existing structure, with capped mounting screws; no routing or bend limits changed.

The isolated servo harness validation on the merged 13dc2ff cable simulation completed with **0 errors, valid=true** in 447.64 seconds. Required support centres (mm) are (311,-10,41.87), (185.211,-49.211,62), (135,-79.347,113), and (85.477,-100,128). Three additional physically placed supports were selected by the planner. This proves the isolated route only; combined harness occupancy and the full steering sweep still require validation.

After promotion, offline CAD recomposition, assembly, model and connectors each reported **0 errors**. Changed CAD types: servo-post-al6061-cable, power-lead-post-petg, servo-south-post-petg (new), servo-approach-post-petg (new), upper-deck-petg. The deck's south tabs and IMU south-post position changed through interfaces.
