# Measured GY-521 mounting integration

The integrator authorized correcting the base catalog pattern to the downloaded
STEP: centres (+/-8.175, +5.825) mm in component coordinates, 16.35 mm pitch,
3 mm holes through the 1 mm laminate. The opposing mount interface uses negative
Y. Provenance remains estimated for the owned board, with the 15.5 / 16.35 /
17.05 mm supplier spread retained. No installation variant exists.

Commits 1bed937 and 68546ab separately correct the pattern and restore the two
cap-seat interfaces unintentionally dropped by part-accuracy. Both seats derive
from the base mount holes. The printed pedestal reflects the source pattern
into its opposing interface and uses the ordinary M2.5 clearance/thread checks.
The two cap rims are relieved around board edges and nearby populated parts;
the purchased board is unchanged.

Offline recomposition previously filled the source-only custom board with its
2.5 mm envelope. The generic source-only STEP path now reads the original faces
with Open CASCADE, without authoring or cutting a purchased part. The ordinary
assembly exact-interference check uses the same supplied solid.

Final local fit evidence at 49827fd:

- Both screw heads bear on 8.139 mm^2 of actual board surface.
- Both screws engage 7.006 mm (geometric bore length 7 mm), against 5 mm required;
  the measured printed tap-drill diameter is 2.05 mm.
- Board/pedestal contact: 70.709 mm^2. Pedestal/deck bond: 500 mm^2.
- Cap bonds: 2.869 and 2.098 mm^2, each 100% of its bearing face, against the
  unchanged 80% contact fraction requirement.
- All screw, cap, retention and interference checks pass. Offline CAD: 0 errors.
- The two unchanged CAD assembly-cleanliness tests: 2 passed in 17.20 s.

Model-stage `mount_geometry_estimated` keeps the owned mount visible as estimated
in release evidence until its holes are measured. The future joint-physics layer
must still be merged and rerun; this report does not claim that unmerged check.
