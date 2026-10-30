# XT60 supplied-model mismatch

The requested power_y.P2 / esc_power.B check remains blocking. No housing was
sculpted and no collision or insertion tolerance changed.

Both installed STEP files are identical to current upstream KiCad master:

- Female: 109524 bytes, SHA-256
  `935d90c13e60840265a66348a2d4c8da96051e99b6726b2b006e2334b7841d7a`.
- Male: 182078 bytes, SHA-256
  `79c46051de822e2939ebbdbe2d708897adffa66bdc61a011b7f559c4a8565679`.

Source: https://gitlab.com/kicad/libraries/kicad-packages3D/-/tree/master/Connector_AMASS.3dshapes
(files AMASS_XT60-F_1x02_P7.2mm_Vertical.step and the corresponding M file).

The correct pin-preserving half-turn about X gives 8.360012 mm^3 of noncontact
housing overlap in the local-frame probe; the robot-frame checker previously
reported 7.467788 mm^3. These use the verifier's measured contact masks, so the
reported values differ with its placement-dependent contact extraction. The
intersection is at the centre key rib, not the two current-carrying contacts.
The alternative half-turn about Y gives 30.267389 mm^3 and exchanges the pins.

Observed insertion is 7.25 mm against 6.30 mm required. An axial gap of 0.95 mm
uses that entire margin but still leaves 7.125000 mm^3 of housing overlap.
A 4 mm gap still leaves 3.160000 mm^3; an 8 mm gap clears the housings but cannot
provide the required insertion. Thus an axial-seat adjustment cannot fix this
pair while preserving contact engagement. Reclocking by a half-turn makes the
key/chamfer mismatch worse.

The public AMASS XT60-M and XT60-F product pages and download centre were checked:
https://www.china-amass.net/xt60-m-product/
https://www.china-amass.net/xt60-f-product/
https://www.china-amass.com/download/
They expose specification PDFs, but no replacement STEP pair was found. The
current official KiCad files already are the pair in use. A compatible real
model pair is still needed; this is not permission to modify purchased geometry
or increase the overlap tolerance. No vendor was contacted.

A further public supplier model was evaluated from Bristol Bot Builders:
https://shop.bristolbotbuilders.com/product/xt60/ links the assembled pair at
https://drive.google.com/file/d/1akDTGfGDaTSRl7Z06hR7Tx2zWON6zQzp/view .
Its STEP SHA-256 is
`1e7216af4bfe05a5c43e71a3bd4d5b2f5f176a3fc0df4c4ef144a595937ee413`.
The two unchanged supplied solids intersect by **651.844886 mm^3** in the
published assembly. Both contain 2.15 mm-radius bores at +/-3.6 mm pitch centres;
the model does not supply separate conductive pin/socket solids. It is useful
as an envelope reference, but cannot replace the current pair for the blocking
mesh-mating/insertion proof. It was not installed, edited or redistributed.
