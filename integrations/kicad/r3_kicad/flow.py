"""Headless KiCad flow for one board: no window is ever opened.

    python -m r3_kicad.flow <pcb/<board> dir> [--from STEP] [--to STEP] [--passes N]

Steps (timings and results logged to ``flow-log.json`` in the board folder):
  schematic  write the sheet with official library symbols (kicad-sch-api), ``kicad-cli sch upgrade``
  erc        ``kicad-cli sch erc``: must report 0 errors
  netlist    ``kicad-cli sch export netlist``; KiCad's netlist must equal the design intent
  board      pcbnew engine: board setup, outline, Update PCB from Schematic (footprints by netlist footprint
             field, pad nets), placement, hand-routed power copper (locked), fill, save
  route      pcbnew Specctra DSN export -> Freerouting CLI -> pcbnew SES import
  pour       pcbnew engine: ground pours on both layers, refill, save
  drc        ``kicad-cli pcb drc --schematic-parity``: must report 0 errors and 0 unconnected items

A :class:`~r3_kicad.headless.GuiWatch` samples the process table (and on macOS the
window server's window list) for the whole run; any KiCad GUI process or any window
owned by KiCad, its Python or Java that appears fails the flow.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import freerouting
from .cli import KicadCli, report_counts, report_items
from .headless import GuiWatch
from .intent import BoardIntent
from .netlist import compare, read_netlist
from .pcb import build_plan, ground_plan, run_plan
from .schematic import write_schematic

STEPS = ("schematic", "erc", "netlist", "board", "route", "pour", "drc")
FLOW_VERSION = "2-headless"
PACKAGE = Path(__file__).parent


class FlowError(RuntimeError):
    pass


def load_intent_module(source: Path):
    """The board folder's design-intent module: a ``*.py`` with ``build()`` and ``schematic_groups()``."""
    for path in sorted(source.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"r3_board_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = module  # type: ignore[union-attr]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        if hasattr(module, "build") and hasattr(module, "schematic_groups"):
            return module
    raise FileNotFoundError(f"no design-intent module (build, schematic_groups) in {source}")


# r3_kicad modules whose code decides what KiCad produces (read-back, checks and reports are excluded).
DESIGN_MODULES = ("intent.py", "regulators.py", "ipc2152.py", "library.py", "schematic.py", "schematic_layout.py",
                  "pcb.py", "flow.py", "freerouting.py", "engine.py")


def inputs_digest(source: Path) -> str:
    """Hash of everything that decides the design: the intent module and the design-producing automation."""
    h = hashlib.sha256(FLOW_VERSION.encode())
    files = (sorted(source.glob("*.py")) + [PACKAGE / name for name in DESIGN_MODULES]
             + sorted((PACKAGE / "kicad_scripts").glob("board_build.py")) + [PACKAGE / "kicad_scripts/specctra.py"])
    for path in files:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _open_nets(drc_report: Path) -> set[str]:
    """Net names ("[/NAME]") of the items in a DRC report's unconnected list."""
    nets: set[str] = set()
    for item in json.loads(drc_report.read_text()).get("unconnected_items", []):
        for part in item.get("items", []):
            text = part.get("description", "")
            if "[" in text and "]" in text:
                nets.add(text[text.index("[") + 1:text.index("]")])
    return nets


def _open_signal_items(drc_report: Path, ground: str = "/GND") -> int:
    """Unconnected items that are not ground (ground gaps are closed by the pours and their repair)."""
    count = 0
    for item in json.loads(drc_report.read_text()).get("unconnected_items", []):
        texts = [part.get("description", "") for part in item.get("items", [])]
        if not any(f"[{ground}]" in text for text in texts):
            count += 1
    return count


class Flow:
    def __init__(self, source: Path, log: Callable[[str], None] = print, passes: int = 100):
        self.source = Path(source).resolve()
        self.module = load_intent_module(self.source)
        self.intent: BoardIntent = self.module.build()
        self.name = self.intent.name
        self.sch = self.source / f"{self.name}.kicad_sch"
        self.pcb = self.source / f"{self.name}.kicad_pcb"
        self.work = self.source / "work"
        self.work.mkdir(exist_ok=True)
        self.cli = KicadCli.find()
        if self.cli is None:
            raise FlowError("KiCad (kicad-cli + pcbnew) is not installed")
        self.log = log
        self.passes = passes
        self.date = time.strftime("%Y-%m-%d")
        self.record: dict[str, Any] = {"flow_version": FLOW_VERSION, "kicad": self.cli.version(), "steps": {},
                                       "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def _save_record(self) -> None:
        (self.source / "flow-log.json").write_text(json.dumps(self.record, indent=2, default=str))

    def _step(self, name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.time()
        self.log(f"== {name}")
        try:
            result = fn()
        finally:
            self.record["steps"].setdefault(name, {})["seconds"] = round(time.time() - t0, 1)
            self._save_record()
        self.record["steps"][name]["result"] = result
        self._save_record()
        return result

    # ------------------------------------------------------------------ steps
    def schematic(self) -> Any:
        report = write_schematic(self.intent, self.module.schematic_groups(), self.sch, date=self.date)
        if report.pin_mismatches:
            raise FlowError("symbol pins differ from the plan: " + "; ".join(report.pin_mismatches[:10]))
        upgraded = self.cli.run("sch", "upgrade", str(self.sch))
        if not upgraded.ok:
            raise FlowError("kicad-cli sch upgrade failed: " + upgraded.stderr[-500:])
        return {"symbols": report.symbols, "labels": report.labels, "no_connects": report.no_connects,
                "writer": "kicad-sch-api, normalized by kicad-cli sch upgrade"}

    def erc(self) -> Any:
        out = self.work / "erc.json"
        self.cli.erc(self.sch, out)
        counts = report_counts(out)
        if counts["errors"]:
            items = [i.get("description") for i in report_items(out) if i.get("severity") == "error"]
            raise FlowError(f"ERC reports {counts['errors']} errors: {items[:10]}")
        return counts

    def netlist(self) -> Any:
        xml = self.work / f"{self.name}.xml"
        if not self.cli.netlist(self.sch, xml).ok:
            raise FlowError("netlist export failed")
        problems = compare(self.intent.nets(), read_netlist(xml))
        if problems:
            raise FlowError("KiCad's netlist differs from the intent: " + "; ".join(problems[:10]))
        return {"nets": len(read_netlist(xml).nets), "matches_intent": True}

    def board(self) -> Any:
        # A project lock left by an interrupted headless run makes pcbnew skip saving the project file (net
        # classes, rules) without an error; the flow never opens the editors, so any lock here is stale.
        lock = self.pcb.parent / f"~{self.name}.kicad_pro.lck"
        if lock.exists():
            self.log(f"   removing stale project lock {lock.name}")
            lock.unlink()
        self._write_custom_rules()
        plan = build_plan(self.intent, self.work / f"{self.name}.xml", self.pcb, date=self.date)
        result, report = run_plan(plan, self.work, "board-plan")
        if not result.ok or report.get("errors"):
            raise FlowError("pcbnew board build failed: " + "; ".join(report.get("errors", [])[:10]))
        return {k: report[k] for k in ("footprints", "tracks", "vias", "zones", "keepouts", "anchored")}

    def _write_custom_rules(self) -> None:
        """Board Setup > Custom Rules: KiCad reads ``<board>.kicad_dru`` beside the project (the same file the
        rule editor saves). The intent lists rules as (name, condition, constraint) S-expression fragments."""
        dru = self.pcb.with_suffix(".kicad_dru")
        rules = self.intent.rules.get("custom_rules") or []
        if not rules:
            dru.unlink(missing_ok=True)
            return
        lines = ["(version 1)"]
        for name, condition, constraint in rules:
            lines += [f'(rule "{name}"', f'  (condition "{condition}")', f"  {constraint})"]
        dru.write_text("\n".join(lines) + "\n")

    def route(self, attempts: int = 3) -> Any:
        """Autoroute; if an attempt stalls with open connections, start over from the hand-routed board
        (all autorouted copper discarded) and autoroute again, up to ``attempts`` times. Freerouting's
        result varies between runs; the best attempt is kept."""
        start = self.work / f"{self.name}-hand-routed.kicad_pcb"
        start.write_bytes(self.pcb.read_bytes())
        chosen = self.work / f"{self.name}-chosen.kicad_pcb"
        tries: list[dict[str, Any]] = []
        for attempt in range(1, attempts + 1):
            self.pcb.write_bytes(start.read_bytes())
            result = self._route_rounds()
            result["attempt"] = attempt
            tries.append(result)
            if min(t["unconnected_after_routing"] for t in tries) == result["unconnected_after_routing"]:
                chosen.write_bytes(self.pcb.read_bytes())
            if result["unconnected_after_routing"] == 0:
                break
            self.log(f"   attempt {attempt} left {result['unconnected_after_routing']} unconnected; starting over")
        self.pcb.write_bytes(chosen.read_bytes())
        best = min(tries, key=lambda t: t["unconnected_after_routing"])
        return {**best, "attempts": len(tries),
                "attempt_results": [t["unconnected_after_routing"] for t in tries]}

    def _route_rounds(self, max_rounds: int = 10, patience: int = 2) -> dict[str, Any]:
        """Autoroute in rounds, like pressing Autoroute again on a partly routed board: each round exports the
        current board (earlier routes included) to Specctra, lets Freerouting continue, and imports the session.
        When a round brings no improvement, the nets that are still open are unrouted (their autorouted copper
        deleted; hand-routed copper stays) and routed again. Stops at 0 unconnected items (KiCad's own DRC),
        after ``patience`` rounds without improvement, or after ``max_rounds``; the best board is kept."""
        router = freerouting.find_router()
        dsn, ses = self.work / f"{self.name}.dsn", self.work / f"{self.name}.ses"
        best = self.work / f"{self.name}-best.kicad_pcb"
        rounds: list[dict[str, Any]] = []
        best_open: int | None = None
        stalled = 0
        for index in range(1, max_rounds + 1):
            exported = freerouting.export_dsn(self.pcb, dsn)
            if not exported.ok:
                raise FlowError("Specctra export failed: " + exported.output[-500:])
            routed = freerouting.route(dsn, ses, passes=self.passes)
            (self.work / f"freerouting-{index}.log").write_text(routed.output)
            if not routed.ok:
                raise FlowError("Freerouting produced no session: " + routed.output[-1500:])
            imported = freerouting.import_ses(self.pcb, ses, fill=True)
            if not imported.ok:
                raise FlowError("Specctra session import failed: " + imported.output[-500:])
            check = self.work / f"route-{index}-drc.json"
            self.cli.drc(self.pcb, check, parity=False, refill=False)
            open_items = _open_signal_items(check)
            open_nets = sorted(_open_nets(check) - {"/GND"})
            entry: dict[str, Any] = {"round": index, "freerouting_unrouted": freerouting.unrouted(routed.output),
                                     "kicad_unconnected": report_counts(check).get("unconnected", 0),
                                     "signal_unconnected": open_items, "open_nets": open_nets}
            rounds.append(entry)
            self.log(f"   round {index}: {open_items} signal connections open {open_nets}")
            if best_open is None or open_items < best_open:
                best_open, stalled = open_items, 0
                best.write_bytes(self.pcb.read_bytes())
            else:
                stalled += 1
            if open_items == 0 or stalled >= patience:
                break
            if stalled and open_nets:
                freerouting.unroute(self.pcb, set(open_nets))
                entry["unrouted_for_retry"] = open_nets
        self.pcb.write_bytes(best.read_bytes())
        return {"freerouting": router.version if router else None, "java": router.java_version if router else None,
                "max_passes": self.passes, "rounds": rounds, "unconnected_after_routing": best_open,
                "note": "signal connections only; ground is completed by the pours"}

    def pour(self) -> Any:
        nets = {n for n in read_netlist(self.work / f"{self.name}.xml").nets}
        plan = ground_plan(self.intent, self.pcb, {f"/{n}" for n in nets} | nets)
        result, report = run_plan(plan, self.work, "pour-plan")
        if not result.ok or report.get("errors"):
            raise FlowError("ground pour failed: " + "; ".join(report.get("errors", [])[:10]))
        return {k: report[k] for k in ("zones", "stitching_vias", "island_vias", "untied_islands")}

    def drc(self) -> Any:
        out = self.work / "drc.json"
        self.cli.drc(self.pcb, out, refill=True)
        counts = report_counts(out)
        counts["items"] = [f"{i.get('type')}: {i.get('description')}" for i in report_items(out)
                           if i.get("severity") == "error"][:30]
        return counts

    def run(self, start: str = "schematic", stop: str = "drc", *, watch_interval: float = .25) -> dict[str, Any]:
        steps = STEPS[STEPS.index(start): STEPS.index(stop) + 1]
        t0 = time.time()
        with GuiWatch(interval=watch_interval) as watch:
            try:
                for name in steps:
                    self._step(name, getattr(self, name))
            finally:
                self.record["headless"] = watch.summary()
                self.record["total_seconds"] = round(time.time() - t0, 1)
                self._save_record()
        if not watch.clean:
            raise FlowError(f"a GUI appeared during the flow: processes {watch.seen}, windows {watch.windows}")
        self.record["inputs_digest"] = inputs_digest(self.source)
        self._save_record()
        return self.record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("board_dir", type=Path)
    parser.add_argument("--from", dest="start", default="schematic", choices=STEPS)
    parser.add_argument("--to", dest="stop", default="drc", choices=STEPS)
    parser.add_argument("--passes", type=int, default=100, help="Freerouting maximum autorouter passes")
    args = parser.parse_args(argv)
    flow = Flow(args.board_dir, log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True), passes=args.passes)
    record = flow.run(args.start, args.stop)
    print(json.dumps({k: v.get("result") for k, v in record["steps"].items()}, indent=1, default=str)[-3000:])
    print("total", record["total_seconds"], "s; headless:", record["headless"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
