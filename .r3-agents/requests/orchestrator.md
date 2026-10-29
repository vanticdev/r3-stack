# Contract requests from the orchestrator area

1. **`Pin.passthrough: bool | None` in `r3_catalog.schema.Pin`.** PCB pins that
   only route a signal between two of the board's connectors (for example the
   HAT's `CRSF_TX`, which goes from `J_GPIO.8` to `J_RX.2`) carry a driver role
   (`uart_tx`) but neither drive nor receive. Without a flag, pin-role checks
   would report a false `uart_tx_tx_conflict` between the Pi and the HAT. The
   model stage reads `getattr(pin, "passthrough", None)` and, when it is unset,
   uses a heuristic: `kind: pcb`, a signal role, exposed on two or more of the
   board's connectors. An explicit field would remove the heuristic.

2. **Clarify the z-axis cylinder origin in `data/components/rc_car.yaml`.** The
   header says cylinders are "centered on their axis at mid-length", but
   `standoff-m3-47` (axis z, 47 mm) only spans chassis top (28 mm) to deck bottom
   (75 mm) if its origin is the bottom face. The model stage and web view use the
   bottom-face origin for z-axis cylinders and the axis midpoint for x/y-axis
   cylinders (wheels, motor). Please state that convention in the catalog header,
   or move the standoff poses.

3. **Add `ruamel.yaml` to the root `pyproject.toml` dependencies** (or to an
   `orchestrator` extra). `r3_patch_spec` uses it to keep `robot.yaml` comments
   and unchanged lines byte for byte. Without it, the patch falls back to PyYAML
   and drops comments (`preserves_comments: false` in the response). It is
   installed in the shared venv (ruamel.yaml 0.19.1).

4. **Console script for the MCP server:** `r3-mcp = "r3_orchestrator.mcp_server:main"`
   in `[project.scripts]`.

5. **The installed `mcp` package is 2.3.0, where `FastMCP` is renamed to
   `MCPServer`.** `mcp_server.py` imports `mcp.server.mcpserver.MCPServer` and
   falls back to `mcp.server.fastmcp.FastMCP` on 1.x. The `mcp>=1.2` pin in
   `pyproject.toml` works with both.

6. **Layout interference in `examples/robots/rc-car/robot.yaml`** (design fix
   for the coordinator, not a contract change). See `r3 validate --stage model`:
   - `standoff_rl` (45, 45 mm) passes through the ESC body (x 41.75 to 88.25, y 23 to 57 mm): 841 mm³.
   - `standoff_rr` (45, -45 mm) passes through the motor can (axis y, center x 50, z 46, Ø36): 815 mm³.
   - `hat` versus `pi`: the Pi envelope is 17 mm tall (USB/Ethernet jacks), and the HAT sits 12.5 mm
     above the Pi PCB, so the envelopes overlap by 4.5 mm over the whole HAT footprint (16 380 mm³).
     In reality the jacks sit outside the HAT footprint, so this is an envelope artifact. A stepped
     Pi envelope (PCB plus jack block) in the catalog would resolve it.
   - `knuckle_fl`/`knuckle_fr` are 0.5 mm into the wheel envelopes (knuckle y 58 to 78, wheel inner
     face at 77.5 mm): 600 mm³ each. The wheel is modeled as a solid cylinder, but a real hub sits
     inside the rim, so this is borderline. Move the knuckles to y = ±66 mm or model the wheel hub recess.
