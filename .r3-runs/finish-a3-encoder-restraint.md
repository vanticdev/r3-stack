# Encoder lead restraint

The Hall-board pigtail exits diagonally outward from its unchanged pads. A capped, chassis-joined PETG tower at (51.79282,-62,28) mm holds the cable with collars at z=58 and 104 mm and an upper return rail at z=120 mm. The third tie is at (45.023,-25,120) mm. The chassis has a local tab for the base screw's full edge annulus and cap bond.

The earlier z=100 mm return rail allowed its span to sag toward the gearbox lid: the exact MuJoCo closest point was near (42.98,-49.92,87.70) mm. Raising that rail by 20 mm and its preceding tie by 16 mm resolved the moving-part clearance without changing any clearance or bend limits.

Isolated encoder validation on the merged 13dc2ff simulation: **0 errors, valid=true**, 112.81 seconds. Offline CAD recomposition, assembly, model and connectors each report **0 errors** after promotion. Combined harness occupancy and the real-firmware steering sweep remain required.

Rebuild CAD types: chassis-plate-al6061, encoder-lead-post-petg. The Hall board's pigtail exit direction changed; its purchased board and mounting geometry did not.
