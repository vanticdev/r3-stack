# Assembly integrity in every simulation

A mount sets a nominal pose; it does not glue parts together. `r3_model.connections.compile_connections`
compiles the resolved assembly joins, mechanical fasteners and interfaces into SI capacities before simulation.
`model/connections.json` records each connection, its inputs, sources, estimates and static errors.
Missing nuts, female threads, engaged material or qualified retention cause `joint_capacity_zero`.
An assembly torque above proof, stripping or punching capacity causes `joint_assembly_overload`.
Neither diagnostic can pass the release gate.

`r3_sim.assembly.build_assembly_model` and `r3_sim.dynamics.RobotDynamics` are the public generic
assembly builder and monitored runtime. They support passive structures, articulated arms and other
non-vehicle robots. The vehicle builder is an adapter for declared wheels, tires and steering; it
uses the same `r3_sim.joints` layer. Every component, screw and load-bearing nut has a massive body.
Native hinge, slide and ball coordinates remain available to actuators. Equality constraints represent
real attachments; contacts alone support unretained resting parts. Connector cable anchors follow
the actual component body. CAD convex decomposition or catalog primitives provide collision shapes.
Hardware uses six convex wedges per hollow nut and thread-core collision proxies (the bore polygon circumscribes the nominal circular bore): nominal helical flank engagement is
represented by its connection capacity rather than by interpenetrating solid cylinders.

## Capacity equations and sources

All quantities are SI. Inputs accept `{value, status, source}` under an interface's `params.physics`.
Statuses are `datasheet`, `standard`, `measured` or `estimated`; anonymous numerical coefficients
are not accepted. Fastener torque and locking features can also be authored in the join's
`fasteners` declaration. `locking` is one of `nyloc`, `threadlocker`, `lock_washer`, `locking_cap`,
`none`. Catalog torque defaults are estimates, not assembly instructions qualified for every material.

- Screws, nuts and threaded inserts: `Fpre = T/(K d)`, slip `mu Fpre n`, metric tensile area
  `At = pi/4 (d - 0.9382 pitch)^2`, proof `At Sp`, thread stripping
  `pi (d - 0.64952 pitch) Le tau_female/2`, head/nut punching `pi Dbearing t tau_plate`.
  Axial capacity is the minimum of preload separation, proof, female stripping and both bearing
  faces. Screw shear is `At Sp/sqrt(3)`. Retained unpreloaded pivots have proof/thread retention
  without clamp friction. A missing nut or female thread has zero axial retention/preload and
  its shank supplies only contact bearing/shear. Moment checks use the corresponding force
  capacity times the contact radius. These are conservative lumped joint limits, without a
  compliant bolt/member load fraction or a fatigue model.
  Sources: [NASA RP-1228, Fastener Design Manual](https://ntrs.nasa.gov/citations/19900009424),
  Shigley's Mechanical Engineering Design, chapter 8, and the
  [VDI 2230 scope](https://www.vdi.de/fileadmin/pages/vdi_de/redakteure/richtlinien/inhaltsverzeichnisse/2360550.pdf).
  This implementation does not claim a complete VDI 2230 qualification.
- Interference fits: Lamé solid shaft/thick open-ended hub,
  `delta/d = p [(1-nu_s)/E_s + ((D^2+d^2)/(D^2-d^2)+nu_h)/E_h]`;
  axial `mu p pi d L`, torsion `Faxial d/2`. A pressure/hoop stress exceeding the weakest
  material strength produces a blocking fit-yield diagnostic. This elastic model requires
  qualified local hub geometry and material data; it is not a plastic deformation model.
- Adhesive/tape: datasheet shear and peel stress times bonded area. Peel force per width
  must first be converted to an applicable effective stress by a qualified coupon; do not
  enter N/m as Pa. Generic fallback strengths and envelope contact areas are explicit estimates.
- Straps/ties: rated tension, including their declared support anchors.
- Connectors: minimum retention and unmating force; an explicit transverse rating may override
  the estimated generic retention value.
- Snap fits: cantilever `min(E b t^3 delta/(4 L^3), sigma b t^2/(6 L))`,
  [BASF snap-fit beam guidance](https://ultrasimweb.basf.com/snapfit/blog/introduction/).
- Ball sockets carry retained force without moment. Bearing/flat/spline interfaces use projected
  bearing area and material strength; axial capacity comes from declared real retainers.
- Resting: no equality or invented holding force.

Printed strength is stored in `materials.yaml`, with print/layer conditions from the original TDS.
Capacity checks conservatively use the weakest XY/Z direction. The shear conversion from ultimate
strength is estimated and is not a measured printed shear allowable. The estimate warning enumerates
all substituted coefficients; the JSON artifact preserves the complete values and sources.

## Native dynamics and failure

After every native step, the monitor projects MuJoCo equality forces through the equality Jacobian
(`J^T lambda`), transforms the wrench to the interface point, and resolves axial, transverse,
torsional and bending loads. Exceeding a capacity deactivates that equality through `data.eq_active`.
Failure of a shared screw clamp releases the associated stack. Collision exclusions for a retained
connection return when all real retention paths between that pair fail. MuJoCo then integrates the
released body and its contacts. A failed declared transmission path also removes its ideal actuator
coupling. No R3 code integrates positions, collision impulses or joint constraint forces.

The [Junker transverse-slip mechanism](https://doi.org/10.4271/690055) reduces preload on unlocked
screws only when transverse load exceeds their friction capacity and relative transverse slip occurs.
The capacity law `fraction *= exp(-slip_distance/decay_distance)` is an explicitly **estimated** rate
requiring joint-specific calibration; the Junker paper does not supply a universal decay distance.
Nyloc, threadlocker, lock washers and declared locking caps disable this loosening rule, not overload.

A free mount carrier surrounds each native moving coordinate. A catalog estimate of `0.0001` of the
part mass and isotropic inertia regularizes the redundant mount frame. The child inertia/COM are
adjusted so total mass, COM and inertia remain exact at every configuration. The effective native-joint inertia and its coupling have an O(0.0001) approximation while the coordinate moves. The fraction is reduced further for slender or offset parts when needed to keep child inertia positive. When its actual mount releases, an
internal gauge equality freezes that coordinate at its current relative pose; the complete rigid part
remains freely mobile. This avoids unobservable carrier spin without retaining the part to the world.
The numerical approximation is reported as `joint_inertia_estimated`; the configured fraction is a maximum, and any reduced per-part value is recorded among the estimates.

MuJoCo uses sparse Newton, pyramidal contact, exact constraint inertia, islands and native sleeping. Cable-host trees stay awake for cable force/clearance evaluation; passive hardware uses the native automatic sleep policy, and actuated trees retain MuJoCo’s no-sleep policy.
`simulation.physics_max_timestep` defaults to 0.25 ms. A master `simulation.timestep` is split into an
integer number of native steps without changing electrical/software timing. Every native step runs
capacity, detachment and cable contact checks. If a catalog-only model begins with rigid collision penetration beyond its fit tolerance, the runtime conservatively refines to at most 50 µs, reports `joint_timestep_reduced`, and records the estimated numerical setting. Qualified CAD models retain the requested maximum step. The first step half can overlap the electrical solve;
with substeps the next electrical solve waits for all of them. Numerical instability is an error and
an incomplete run never qualifies. No check or physical tolerance is disabled to improve speed.

## Requirements and mandatory scenarios

These scenarios are appended to every project's authored scenarios, including future ones. Their IDs
are reserved so an authored test cannot replace them.

| Requirement | Default and cases |
| --- | --- |
| Gravity | 2 s each: upright, inverted, left, right, front, rear; only the base is held by the test fixture |
| `requirements.drop_height` | 1 m clearance, six faces and eight bounding-box corners; native gravity and contacts, fixture released |
| `requirements.impact_speed` | `top_speed`; moving robots hit a rigid head-on wall. Stationary robots emit `joint_impact_not_applicable` and retain gravity checks |
| `requirements.vibration` | 60 s per axis, random acceleration PSD plus motor harmonic; source and all profile values are spec-overridable |

`robot.mobility` can explicitly be `stationary` or `mobile`. `simulation.base_mount` can be `fixed`,
`free` or `auto`; auto fixes a stationary base to the world and releases it for drop cases. Without
an explicit choice, positive translational top/impact speed indicates mobility. This choice does not
infer mobile behavior from an articulated joint, wheel count or robot name.

The vibration default follows the tailoring approach of
[MIL-STD-810H Method 514.8, Annex C](https://quicksearch.dla.mil/qaDocDetails.aspx?ident_number=35978).
It is an **estimated small-platform profile**, not a verbatim standard qualification spectrum:
PSD breakpoints `(Hz, (m/s²)²/Hz)` are `(5,.05), (20,.2), (80,.2), (200,.02)`;
the motor term is 120 Hz at 1 m/s² peak, with a 1 s smooth ramp. A seeded Fourier realization
drives the fixture through analytically integrated displacement. The same profile is available to
arms, drones, boats and stationary rigs; their environment-specific qualified profile should override
these defaults. No wheel, motor or road is required to run the generic excitation.

Every result stores per-connection peak utilization, remaining preload and failures. `joint_failed`
and `part_detached` are blocking errors; `joint_utilization_high` starts at 0.8. Detachment compares
the parent-relative fit pose against its declared/catalog tolerance, preserving allowed native DOFs.
The release gate requires current evidence for all authored and mandatory cases. Recording NPZ files
contain world poses of every part and joint events independently of the generated XML. The web viewer
replays screws/nuts and released components; the failure list seeks to the event and states the load,
capacity and cause.

## Validation fixtures

`tests/test_joint_physics.py` exercises hand-calculated screw/fit/snap capacities, retained and missing-nut
plates in all gravity orientations, tape peeling, contact-only resting parts, locked/unlocked native
vibration, arbitrary scenarios and a stationary two-link arm using the public assembly API. Existing
vehicle dynamics and HAL performance checks use an intact generic mounting bench, while the actual
reference robot's unqualified connections remain failures. Never repair the reference design inside
the simulator or suppress its diagnostics to recover a legacy pass.
