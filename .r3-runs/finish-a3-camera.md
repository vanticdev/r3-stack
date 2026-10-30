# Provisional owned UC-717 camera installation

The owner's hole measurements are pending. The camera catalog's `mount` interface
is the sole source of the assumed four 2.5 mm holes at (+/-15, +/-15) mm: 30 x 30 mm
centre spacing. `rc-camera-mount-petg.camera` uses `holes_from`; the four camera
cap seats use `hole_seat`. Updating this interface updates the fixture, proxy
holes and cap positions. M2x8 screws fit the assumed clearance holes; M2.5 screws
do not satisfy the existing ISO clearance rule. This is not a released mount.

`mount_pattern_estimated` names `front_camera`, the assumed diameters and centre
spacing. The fabrication gate must retain this estimated status until the owner
confirms the dimensions. No drawing was found that confirms this pattern.

The camera is owned, front facing (+X, image up +Z), retained through a joined
PETG bracket and capped fasteners. The included 150 mm 22-pin cable connects to
Pi CAM0; its camera end is retained by a 3M 4026 tape strip. Its free-end frame is
derived from the Pi socket. **The complete ribbon route is not verified**:
`flat_cable_route_estimated` explicitly gates release pending bends, intermediate
restraints and swept-clearance verification. Endpoint reach is not that proof.

Offline recompose, assembly, model and connectors: 0 errors. Nine CAD/pattern/
flexible-cable tests pass. A headless camera HAL probe returned five RGB frames
(320 x 240, sequences 0..4 at 30 Hz), no server errors.

The added camera load initially exposed an electrical budget failure: at
`servo_stall_compute@minimum`, the 5 A fuse carried 3.822178 A continuous against
its existing 75% derated limit of 3.750 A. The current routed 14 AWG pair has an
installed ampacity of 13.4424 A (the earlier 7.03 A figure no longer applies).
The holder now takes a Littelfuse 028707.5 ATOF 7.5 A fuse: 10.91 mOhm cold
resistance, typical melting I2t 60 A2s, from the manufacturer's 287 ATOF table.
The unchanged 75% limit is 5.625 A. The isolated electrical stage passes; the
short-circuit regression blows the fuse at 0.105 s with 110.827 A, then reports
brownout and regulator UVLO at 0.106 s. Final-route ampacity must be rechecked.

Source: https://www.littelfuse.com/assetdocs/littelfuse-datasheet-287-atof?assetguid=43dcdce8-8ca2-426f-8998-7e566f048d40
