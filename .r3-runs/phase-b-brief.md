# Phase B: RC car as joined objects + an organized, human-quality Onshape document

Inputs:
- the direct-CAD builder (`r3/direct-cad`, merged into integration): standard
  features only, fit verification on native geometry;
- the assembly compiler (`r3_model/assembly.py`, `docs/architecture/assembly-model.md`):
  objects + interfaces + joins; poses are derived;
- the policies in `.r3-agents/COMMON.md`: no FeatureScript; use tools like a human;
  headless (REST/API keys; the browser bridge only as a local fallback); integrate
  real simulators.

## 1. Convert the RC car to the object/join model
- Every catalog component type used by the RC car gets `interfaces` with real
  datasheet frames: hole patterns, faces, shafts, bores, splines, headers, connector
  bodies, strap seats and adhesive pads.
- `robot.yaml` gets `assembly: {root: chassis, joins: [...]}` covering every component
  and every fastener the direct-CAD branch introduced. Authored poses are removed, except
  the root's.
- Fabricated parts (plate, deck, standoffs, arms, knuckles, posts, horn, tie rods, axles,
  magnet carrier, antenna mast) are designed so their interfaces receive the purchased
  parts. Their hole positions and seats derive from the joins they serve.
- The compile must produce zero misfits and zero `object_not_joined`, and its exact
  interference must be clean.
- Reroute the wiring problems found by MuJoCo cables: the W_MOT_B motor lead is inside
  the right-rear tire, and the encoder/GPS leads rest on the gearbox.
- Apply the queued design fixes that touch mechanics (`.r3-runs/design-fix-queue.md`):
  - fuse at the splice;
  - encoder on the differential carrier/spur output;
  - antenna mast geometry → `rf.antenna_position`;
  - battery strap.
  Leave electrical-only items to their stages.

## 2. Onshape document structure, the way an experienced engineer organizes it (user request)
The current document is one Part Studio with a flat list of default-named
sketches/extrudes; the user called that out. Required structure:
- **Document tab folders:**
  - `01 Variables` (a Variable Studio with the key parameters: wheelbase, track, plate
    thickness, deck height, wheel size, and so on, referenced by features as `#wheelbase`)
  - `02 Fabricated parts`
  - `03 Purchased parts`: these may instead live in a separate reusable document,
    "R3 Parts Library", that is linked and inserted. Prefer that, because a human reuses
    vendor parts across robots.
  - `04 Subassemblies`
  - `05 Top-level assembly`
  - `06 Exports`, if any
- **One Part Studio per object type** (or tightly related part families), named for the
  part ("Chassis plate", "Steering knuckle", "Raspberry Pi 5"…), each modeled at its own
  origin.
- **Within each Part Studio:**
  - every feature has a descriptive name: "Plate outline sketch", "Plate extrude 3 mm",
    "M3 deck-standoff clearance holes", "Kingpin bore Ø3.2". No default names
    ("Sketch 1", "Extrude 3").
  - features are grouped into feature folders in a logical rollback order, e.g. Base
    shape / Mounting holes / Bores & shafts / Cutouts / Edge treatment / Mate connectors.
  - sketches are constrained and dimensioned, so a human can edit them.
  - every interface has a named mate connector (`mc_<interface>`).
- **Subassemblies:** Rear drivetrain (transmission, motor, axles, wheels), Front steering
  L and R (arm, knuckle, kingpin, stub axle, wheel, nuts), Steering linkage (servo, horn,
  tie rods), Deck & electronics stack (standoffs, deck, Pi, HAT, receiver, GPS, mast),
  Power (battery, strap, fuse). Inside each one, use instance folders (Fasteners,
  Electronics, Structure) and named mates ("Fasten motor to plate", "Revolute steer FL")
  grouped into mate folders.
- **Top-level assembly:** subassemblies mated together, with flexible subassemblies where
  steering must move. Mates sit on the interface mate connectors, so Onshape's solver
  places everything. R3 verifies that the solved transforms equal the compiled poses.
- Part properties: part number, vendor, material and mass source.

## 3. An organization check (permanent; diagnostics fail the cad stage)
Codes:
- `cad_default_feature_name` (regex `^(Sketch|Extrude|Revolve|Fillet|Chamfer|Hole|Mate connector|Plane|Fastened|Revolute)\s*\d+$`)
- `cad_feature_not_in_folder`
- `cad_tab_not_in_folder`
- `cad_part_name_mismatch`
- `cad_missing_mate_connector` (an interface without `mc_`)
- `cad_unconstrained_sketch` (the sketch's DOF reported by Onshape, if exposed)
- `cad_assembly_unfoldered`
- `cad_missing_variable_reference` (a key dimension hard-coded where a variable exists)

All of these must be read HEADLESSLY via REST: features list with folder nodes, document
element folders, assembly definition. Discover the REST operations in the bundled
openapi (`/Users/godbrigero/Documents/onshape-stuff/onshape-native/data/openapi.json`).
If an organizational write, such as feature folders, exists only as a native/browser
operation, implement it through the onshape-native bridge for local runs, and document
the headless gap precisely in `docs/architecture/headless.md`.

## 4. Process
- Build from scratch in the folder. Delete every failed attempt, and keep exactly one live
  document (plus the parts library if you split it out), pinned in `r3.lock.yaml`.
- Run the negative tests: a deliberately misaligned hole pattern, a default-named feature,
  and an unfoldered tab. Each must produce its diagnostic.
- Screenshots: a headless REST shaded view of each subassembly and the top level. If the
  browser bridge is available locally, also capture the feature tree / sidebar to show
  the organization.
- `pytest tests` passes, using `/Users/godbrigero/Documents/r3-stack/.venv/bin/python`.
