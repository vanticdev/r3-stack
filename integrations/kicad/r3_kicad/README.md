# r3_kicad: headless KiCad stage

The `pcb` stage builds each board in `robot.yaml` `pcb.boards` with KiCad's own engine, without ever opening a
window. The design intent (official library parts, nets, placement, hand-routed power copper, mounting) lives
in the board folder (`examples/robots/robot/pcb/carrier-hat/carrier_hat.py`).

```sh
# regenerate the KiCad project (schematic -> ERC -> netlist -> board -> Freerouting -> pours -> DRC)
PYTHONPATH=integrations/kicad python -m r3_kicad.flow examples/robots/robot/pcb/carrier-hat
# stage: reuses the project if the inputs are unchanged, then ERC/DRC, exports, board.json, board.step
python -c "from r3_model.project import Project; from r3_kicad import stage; stage.run(Project.load('examples/robots/robot'))"
```

| Step | Tool (headless) |
| --- | --- |
| Schematic capture | `kicad-sch-api` (structured KiCad-format writer, official symbols embedded verbatim); KiCad 10 has no headless schematic-editing API |
| Normalize / ERC / netlist | `kicad-cli sch upgrade`, `kicad-cli sch erc`, `kicad-cli sch export netlist` |
| Board setup, Update PCB from Schematic, placement, power copper, keep-outs, zones, fill | `pcbnew` Python module in KiCad's interpreter (`kicad_scripts/board_build.py`) |
| Autorouting | `pcbnew.ExportSpecctraDSN` -> Freerouting 2.5 jar (`-Djava.awt.headless=true --gui.enabled=false`) -> `pcbnew.ImportSpecctraSES`, in rounds |
| DRC (schematic parity), Gerbers, drill, pos, BOM, STEP, PDF, renders | `kicad-cli pcb ...` (`kicad-cli pcb render` is an offscreen raytrace) |
| Read-back | `pcbnew` (`kicad_scripts/board_dump.py`) and KiCad's XML netlist |

`r3_kicad.headless.GuiWatch` samples the process table during the flow and the exports. On macOS it also
samples the window server's window list. The flow fails if any KiCad GUI process, or any window owned by
KiCad, its Python or Java, appears. `flow-log.json` records the samples.

## Server (Linux) equivalents

Verified on macOS (KiCad 10.0.7) only; the Linux path below is the documented equivalent and has not been run here.

- KiCad 10 from the KiCad PPA (`add-apt-repository ppa:kicad/kicad-10.0-releases; apt install kicad`), or the
  `kicad/kicad:10.0` image. Both provide `/usr/bin/kicad-cli`, the libraries in `/usr/share/kicad/{symbols,
  footprints,3dmodels}`, and the `pcbnew` module for the system `python3` (`python3-pcbnew` / `kicad` package).
  `r3_kicad.library.find_install` picks them up. Override with `R3_KICAD_CLI`, `R3_KICAD_PYTHON` and
  `R3_KICAD_SHARE`.
- No X server is needed. `kicad-cli` and `pcbnew` run without a display; do not use Xvfb. On a GPU-less host,
  `kicad-cli pcb render` falls back to Mesa's software OpenGL (`LIBGL_ALWAYS_SOFTWARE=1`).
- Freerouting: `freerouting-2.5.0.jar` in `~/.cache/r3/freerouting/` and a Java 25 runtime in
  `~/.cache/r3/java/` or on `PATH`. `R3_FREEROUTING_JAR` and `R3_JAVA` override them. The native
  `freerouting-cli` 2.5.0 build is not used (its GraalVM image fails with a serialization error).
- Python packages in the R3 environment: `kicad-sch-api` (0.5.6) and its dependency `sexpdata`.
