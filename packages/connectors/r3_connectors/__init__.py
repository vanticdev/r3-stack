"""R3 connector and pin geometry: exact pin maps, mated poses, 3D models and renders.

* ``frames``: rigid frames and the mated-half transform.
* ``derive``: builds ``r3_catalog/data/connector_geometry.yaml`` from KiCad footprints/models and datasheets.
* ``pins``: robot-frame connector frames, contact positions and wire entries (component and harness ends).
* ``models``: model resolution (KiCad library, vendor STEP, KiCad board export, proxies) and STEP -> GLB.
* ``stage``: ``run(project)`` writes ``build/connectors/`` (GLBs, connectors.json, renders).
"""
