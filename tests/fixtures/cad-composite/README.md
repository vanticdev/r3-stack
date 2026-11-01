# Native compound export fixture

Two synthetic 10 mm cubes sharing one edge, imported as STEP and grouped with
Onshape's standard closed Composite Part feature. These are unrelated test
solids, not robot components. STL, glTF and STEP are unchanged official native
exports from one immutable version. No part catalog or model-bank data is used.
The throwaway source document is trashed after verification.

STL loses body ownership and has one four-face edge after global exact vertex
joining. The glTF has two native Body nodes (six face primitives each). Both
bodies independently remain closed positive-volume solids. STEP retains two
solids. STEP is stored losslessly as `compound.step.gz` to preserve its exact
exported bytes (including whitespace in its header). The SHA-256 below names
the decompressed original. Readers must preserve that topology without repairing or dropping facets.

- `compound.gltf`: SHA-256 `8346b5583c52ff2c4075156cc5465137c7c819c2a0e38786bbb7808be06f4a00`
- `compound.step`: SHA-256 `c6f2c5a99119b8efffc309ed206241e7dd5bdcd705bb3bad5bfe09b8428f949f`
- `compound.stl`: SHA-256 `70ea82de10d750e5dd4a7fe28f379a9be9ff9c0df827467a61bba27a5718a002`

`original-body-ids.gltf` is another unchanged native export of the same two-cube
fixture with `exportCompositeConstituentsWithOriginalBodyIds=true`. That option
collapses the two native Body nodes into one mesh, retaining all 24 facets but
losing ownership at the shared edge. Its SHA-256 is
`2fb9090cecea3fc785e82677b5f9b44ad8db3be79c4551941446f679d2b9de9a`.
R3 must omit that option for glTF and continue rejecting the collapsed file;
it must never repair its topology locally. A fresh native export without the
option reproduces `compound.gltf` byte for byte.
