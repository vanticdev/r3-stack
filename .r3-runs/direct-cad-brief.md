# Direct CAD + definitive fit verification (supersedes the FeatureScript parts of fit-verification-brief.md)

User directive: no FeatureScript in any form. The AI builds CAD directly, the way a
human does in Onshape: sketches with geometry, constraints and dimensions; standard
features (extrude, revolve, hole, fillet, chamfer, linear/circular pattern, mirror,
boolean, transform, cPlane, mateConnector); and an Assembly with standard mates.
`r3_onshape/policy.py` enforces this. The onshape-native `evaluate` tool is denied.

Proven feasible (coordinator probe):
- `POST /partstudios/d/{did}/w/{wid}/e/{eid}/features` with
  `{"btType":"BTFeatureDefinitionCall-1406","feature":...}`.
- Sketch: `BTMSketch-151`, `featureType: newSketch`, entities `BTMSketchCurveSegment-155`
  (line: `BTCurveGeometryLine-117` with `pntX/pntY/dirX/dirY`, `startParam 0`,
  `endParam` = length) and `BTMSketchCurve-4` (circle: `BTCurveGeometryCircle-115`).
  Use COINCIDENT constraints between line endpoints (`localFirst`/`localSecond` =
  `"<entity>.end"`/`"<entity>.start"`).
- Sketch plane query: `BTMIndividualQuery-138` with the default plane geometry ids
  `deterministicIds` Top=`JDC`, Front=`JCC`, Right=`JEC`.
- Extrude: `featureType: extrude` with `entities` =
  `BTMIndividualSketchRegionQuery-140 {featureId, filterInnerLoops:true}`, and params
  `bodyType=SOLID`, `operationType=NEW|ADD|REMOVE`, `endBound=BLIND`, `depth` "3 mm".
  This regenerated OK, and the volume was exact.
- `GET .../bodydetails` returns every face with its surface definition
  (`BTPlaneDescription-692` origin+normal, `BTCylinderDescription-686` origin+axis+radius)
  and ids usable as `deterministicIds` for holes, fillets and mate connectors.
- Mass properties, tessellatedfaces and parts listing work, as in the existing `sync.py`.
- Use `feature_template` / `search_commands` from the onshape-native MCP to get exact
  live parameter specs for `hole`, `fillet`, `revolve`, `cPlane`, `transform`,
  `mateConnector`, assembly mates and insert-instance. Run `getFeatureSpecs` on a
  part studio for the standard feature specs.

## Build
Write `integrations/onshape/r3_onshape/direct.py` with
`build_robot(api, doc, project, result) -> meta` and `input_digest(project)`. This is
the pluggable builder that `sync.sync()` and `stage.run()` already call; read them.

- **Parts.** Each component and each fastener, strap and magnet is one named part,
  modeled in place in the robot frame. Use sketch planes from cPlane offsets of the
  standard planes or existing faces, with driving dimensions, so a human can edit it.
  Use transform for rotated parts.
- **Fabricated parts** get real geometry: the plate outline with its holes and slots;
  standoffs with tapped holes using the `hole` feature (ISO tapped/clearance); knuckles
  with kingpin bores and stub-axle bores; arms, servo posts, horn, tie rods with
  ball-socket ends, axles with D-flats and threaded ends, the magnet carrier, and the
  antenna mast.
- **Purchased parts** are envelopes, with their catalog datasheet features cut or added
  (mount holes, D-bores, shafts, splines, GPIO header pins). Never cut holes into a
  purchased part from spec fastener positions.
- **Materials and mass.** Fabricated parts get a real material and density, so their
  mass comes from geometry. Purchased parts get the catalog mass. Prefer a mass
  override, labeled `catalog`, over density fudging if the REST/native metadata API
  allows it. Discover the material/mass APIs with `search_commands`.
- **Assembly.**
  - Create an Assembly element and insert every part.
  - Use fastened mates for static parts, at mate connectors on the real hole axes and
    seating faces.
  - Use revolute mates for every revolute/continuous joint, placed on the verified
    interface axis (kingpin screw axis, stub axle axis, axle axis), and ball mates for
    tie-rod ends.
  - The assembly is the kinematic source of truth. Read it back with
    `getAssemblyDefinition` / `element_tree`. Verify every joint in robot.yaml has a
    matching mate with an identical axis, and that mate DOF/limits match.
  - Use `mate_animation` to drive the steering through its range for screenshots.

## Verify (no FeatureScript)
Use native data only: bodydetails surface definitions, mass properties, bounding
boxes, and fine tessellation meshes (chord tolerance ≤ 0.05 mm) with Python mesh
booleans: manifold3d + trimesh, installed in the shared venv. Report the tessellation
error bound in the assumptions.

- **Interference:** intersection volume per pair.
- **Clearance:** minimum distance.
- **Contact area:** coplanar opposing plane faces from bodydetails, plus 2D polygon
  overlap of their tessellated outlines, using manifold3d `CrossSection`.
- **Coaxial fits, D-flats, spheres:** from the surface definitions.
- **Thread engagement:** measured from the screw↔tapped-hole intersection volume, as
  in the earlier brief.
- **Tool access:** cylinders.
- **Motion sweep:** apply the solved linkage transforms to meshes in Python.

Every check, rule, threshold and diagnostic code from `fit-verification-brief.md`
sections 1, 2, 4 and 5 still applies: spec/catalog additions, the RC-car mechanical
redesign, fastener/interface/joint/transmission/retention/motion checks, and the
cache/identity fixes. Only the implementation changes from FeatureScript to native
geometry plus Python.

## Process
- The current pinned document (attempt-6, 314246dec6d07475f0ca7c44) was built with
  FeatureScript and violates the policy. Delete it once your first direct build
  passes.
- Every failed attempt must be deleted; sync does this.
- Run negative tests with deliberately broken builds, and record their diagnostics in
  `build/cad/negative_tests.json`.
- Save screenshots (isometric, front steering, rear drivetrain, and the assembly at
  full lock) with the onshape-native `capture_viewport` tool.
- Write unit tests without Onshape, using fixture JSON recorded from real
  bodydetails/tessellation.
