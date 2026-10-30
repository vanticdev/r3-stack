# GPS and RTK lead restraint

The gearbox-lid GNSS riser now includes a forward shelf for an adhesive tie saddle and a collared post for the short SiK UART lead. The purchased GNSS and radio bodies are unchanged. The GPS tie centre is (44,5.5,102.453) mm; the RTK tie is (30,-18.023,113) mm. These prevent the GPS span sagging toward the gearbox and split the RTK span below its 61.9 mm unsupported-length limit.

Combined GPS/RTK isolated validation: **0 errors**, both harnesses **valid=true**, 48.98 seconds. After promotion alongside the encoder and servo restraints, offline CAD, assembly, model and connectors each report **0 errors**. All-harness occupancy and the firmware sweep remain to be checked.

Onshape rebuild required: gnss-riser-petg.

## Measured-model follow-up

The RTK radio is now 7 mm farther forward, at x=42 mm, by shortening its printed mast arm from 44.1 to 37.1 mm. Its short direct pigtail meets the unsupported-span check without the old GNSS-riser post; that unused post and collar have been removed. The GPS saddle shelf remains.

With the encoder lower tie on the inboard face of its existing collar at (51.79282,-55.20718,58) mm, the combined encoder/GPS/RTK subset passes with **0 errors and all three harnesses valid** (172.62 s). MuJoCo minimum moving-part clearance is **8.970975 mm**, encoder VCC to gearbox at 0.068 s. Promoted offline recomposition, assembly and model each have **0 errors**. This subset result does not establish full-harness validity.

Additional rebuild: sensor-rtk-mast-petg.
