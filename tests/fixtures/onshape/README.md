`transform-defaults.json` records generic standard Transform dialog defaults
from Onshape's native Part Studio `/featurespecs` metadata (2026-10-30).
Ephemeral node IDs were removed. It contains no document IDs, purchased-part
data or robot geometry. The regression verifies a synthetic translated import
supplies all native branches without changing their default semantics.
