"""Scaffold an R3 robot project repository and (only on request) its GitHub remote.

Layout (see ``r3_model.project``)::

    robot.yaml  r3.lock.yaml  README.md  AGENTS.md  CLAUDE.md  .gitignore  .mcp.json
    cad/  pcb/<board>/  catalog/components/  backend/deploy.py  backend/<language>/<module>/
    .github/workflows/r3.yml

``create_remote`` is the only function with an external side effect; it runs
``git`` and ``gh repo create`` and is never called implicitly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

from r3_model.spec import RobotSpec
from r3_model.spec_files import dump_yaml, load_spec_files, roundtrip

from .lock import git_revision, pin

TEMPLATES = Path(__file__).parent / "templates"
PLATFORM_ROOT = Path(__file__).resolve().parents[3]
# The r3-platform pip source written into CI. Override with $R3_PLATFORM_URL.
DEFAULT_PLATFORM_URL = os.environ.get("R3_PLATFORM_URL", "git+https://github.com/vanticdev/r3-stack.git")
# The R3 worker image (docker/r3-worker) CI runs in, tagged by platform revision
# by the platform's own workflow. Override the repository with $R3_WORKER_IMAGE_REPO.
DEFAULT_WORKER_IMAGE_REPO = os.environ.get("R3_WORKER_IMAGE_REPO", "ghcr.io/vanticdev/r3-worker")
TEMPLATE_DIRS = ("cad", "pcb", "backend", "catalog", "parts", "electrical", "software", "mechanical", "simulation", "fabrication")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def examples_dir() -> Path:
    return Path(os.environ.get("R3_EXAMPLES_DIR", PLATFORM_ROOT / "examples" / "robots"))


def available_templates() -> list[str]:
    found = sorted(p.parent.name for p in examples_dir().glob("*/robot.yaml"))
    return [*found, "blank"]


def _render(template: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / template).read_text()
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", value)
    return text


def render_workflow(platform_url: str | None = None, platform_ref: str = "main", worker_image: str | None = None) -> str:
    """The GitHub Actions workflow for an R3 project. It runs in the R3 worker
    image for ``platform_ref`` unless ``worker_image`` names another one."""
    image = worker_image or f"{DEFAULT_WORKER_IMAGE_REPO}:{platform_ref}"
    return _render("r3.yml.j2", {"PLATFORM_URL": platform_url or DEFAULT_PLATFORM_URL, "PLATFORM_REF": platform_ref,
                                 "WORKER_IMAGE": image})


def _rename_robot(text: str, name: str) -> str:
    """Set ``robot.name`` without reformatting the rest of the file."""
    lines = text.splitlines(keepends=True)
    in_robot = False
    for index, line in enumerate(lines):
        if re.match(r"^robot:\s*$", line):
            in_robot = True
            continue
        if in_robot and re.match(r"^\S", line):
            break
        if in_robot and (match := re.match(r"^(\s+name:\s*)\S.*?(\s*)$", line)):
            lines[index] = f"{match.group(1)}{name}{match.group(2)}"
            break
    result = "".join(lines)
    if yaml.safe_load(result)["robot"]["name"] != name:
        loader, data = roundtrip(result)
        data["robot"]["name"] = name
        result = dump_yaml(loader, data)
    return result


def _identifier(name: str) -> str:
    return re.sub(r"\W", "_", name).upper().strip("_") or "MODULE"


def render_deploy(project_name: str, spec: RobotSpec) -> str:
    python_modules = [m for m in spec.software.modules if m.language == "python"]
    others = [m for m in spec.software.modules if m.language != "python"]
    process_types = [f'    {_identifier(m.name)} = "{m.name}", 1.0' for m in python_modules] or ["    pass"]
    declarations = [f'        modules.python("{m.name}", process=ProcessType.{_identifier(m.name)}, entrypoint="{m.entrypoint or "main.py"}"),'
                    for m in python_modules]
    declarations += [f'        # {m.language} module "{m.name}": declare with modules.{m.language}(...) (docs/deployment/deployment-examples.md)'
                     for m in others]
    placements = [f"        .add(ProcessType.{_identifier(m.name)})" for m in python_modules]
    return _render("deploy.py.j2", {"NAME": project_name, "PROCESS_TYPES": "\n".join(process_types),
                                    "MODULES": "\n".join(declarations), "PLACEMENTS": "\n".join(placements)})


def create_project(target: str | Path, name: str | None = None, template: str = "blank",
                   platform_url: str | None = None, platform_ref: str | None = None) -> list[str]:
    """Create a new project directory from ``template`` and return the files written (relative paths)."""
    target = Path(target)
    name = name or target.name
    if not _NAME.match(name):
        raise ValueError(f"invalid project name {name!r}: use letters, digits, '.', '_' or '-'")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} already exists and is not empty")
    if template == "blank":
        source = None
        spec_text = _render("robot.blank.yaml.j2", {"NAME": name})
    else:
        source = examples_dir() / template
        if not (source / "robot.yaml").exists():
            raise KeyError(f"unknown template {template!r}; available: {', '.join(available_templates())}")
        original = load_spec_files(source)
        renamed = dict(original.texts)
        origin = original.origin(("robot", "name"))
        if origin.file == "robot.yaml" and origin.path == ("robot", "name"):
            renamed["robot.yaml"] = _rename_robot(renamed["robot.yaml"], name)
        else:
            loader, data = roundtrip(renamed[origin.file])
            if origin.path:
                target_value = data
                for key in origin.path[:-1]:
                    target_value = target_value[key]
                target_value[origin.path[-1]] = name
            else:
                data = name
            renamed[origin.file] = dump_yaml(loader, data)
        spec_text = renamed["robot.yaml"]
    if source is None:
        descriptions = {"robot.yaml": spec_text, "electrical/system.yaml": "# Electrical harnesses and direct mates.\n{}\n",
                        "software/system.yaml": "# Software modules, hardware bindings and runtime configuration.\n{}\n"}
        loaded = load_spec_files(TEMPLATES, descriptions)
    else:
        loaded = load_spec_files(source, renamed)
        descriptions = dict(loaded.texts)
    spec = loaded.validate()
    loader, index = roundtrip(spec_text)
    components = index["components"]
    for cid, component in ([] if "$include" in components else components.items()):
        if isinstance(component, dict) and "$include" in component:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", cid):
            raise ValueError(f"part id cannot be used as a filename: {cid!r}")
        relative = f"parts/{cid}.yaml"
        descriptions[relative] = dump_yaml(loader, component)
        index["components"][cid] = {"$include": relative}
    for section in ("electrical", "software", "mechanical", "simulation", "fabrication", "pcb"):
        if section in index and not (isinstance(index[section], dict) and "$include" in index[section]):
            relative = f"{section}/system.yaml"
            descriptions[relative] = dump_yaml(loader, index[section])
            index[section] = {"$include": relative}
    descriptions["robot.yaml"] = dump_yaml(loader, index)

    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    def write(relative: str, content: str, overwrite: bool = False) -> None:
        path = target / relative
        if path.exists() and not overwrite:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        written.append(relative)

    (target / "parts").mkdir(exist_ok=True)
    if source is not None:
        for folder in TEMPLATE_DIRS:
            if (source / folder).is_dir():
                shutil.copytree(source / folder, target / folder, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("build", "__pycache__", "*.pyc", ".venv"))
                written += sorted(str(p.relative_to(target)) for p in (target / folder).rglob("*") if p.is_file())

    for relative, content in descriptions.items():
        write(relative, content, overwrite=True)

    first_scenario = spec.simulation.scenarios[0].id if spec.simulation.scenarios else "<id>"
    ref = platform_ref or git_revision(PLATFORM_ROOT) or "main"
    values = {"NAME": name, "PLATFORM_URL": platform_url or DEFAULT_PLATFORM_URL, "PLATFORM_REF": ref,
              "FIRST_SCENARIO": first_scenario}
    write("README.md", _render("README.md.j2", values))
    write("AGENTS.md", _render("AGENTS.md.j2", values))
    write("CLAUDE.md", _render("CLAUDE.md.j2", values))
    write(".gitignore", _render("gitignore.j2", values))
    write(".mcp.json", _render("mcp.json.j2", values))
    write(".github/workflows/r3.yml", render_workflow(values["PLATFORM_URL"], ref))
    write("cad/README.md", "# CAD\n\nModel parts, mate connectors and mates directly in Onshape using standard editable features. "
                           "R3 reads a pinned, immutable local copy under `cad/pulled/`. The description index is `robot.yaml`; "
                           "part metadata lives in `parts/<id>.yaml`. Fields under `pulled` are owned by the pull path.\n")
    for board in spec.pcb.boards:
        write(f"{board.source}/README.md", f"# {Path(board.source).name}\n\nKiCad generator inputs for component `{board.component}` "
                                           f"({board.layers} layers, {board.copper_oz} oz). `r3 pcb build` generates the KiCad project.\n")
    write("catalog/README.md", "# Project catalog\n\nComponent (`components/*.yaml`), connector (`connectors.yaml`) and wire "
                               "(`wires.yaml`) additions merged over the platform catalog. Record provenance for every number.\n")
    write("catalog/components/.gitkeep", "")
    write("backend/deploy.py", render_deploy(name, spec))
    for module in spec.software.modules:
        if module.language == "python":
            write(f"backend/python/{module.name}/{module.entrypoint or 'main.py'}", _render("main.py.j2", {"MODULE": module.name}))
    write("backend/python/common/__init__.py", "")
    pin(target, "platform", ref, url=values["PLATFORM_URL"], package="r3-platform")
    written.append("r3.lock.yaml")
    return sorted(set(written))


Runner = Callable[..., subprocess.CompletedProcess]


def create_remote(name: str, directory: str | Path, private: bool = True, description: str = "",
                  runner: Runner = subprocess.run) -> dict[str, Any]:
    """Commit the scaffold and create + push a GitHub repository with ``gh``.

    External side effect: only call this when a human asked for a remote."""
    directory = Path(directory)
    commands: list[list[str]] = []

    def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
        commands.append(command)
        return runner(command, cwd=directory, capture_output=True, text=True, check=check)

    try:
        if not (directory / ".git").exists():
            run(["git", "init", "-b", "main"])
        run(["git", "add", "-A"])
        if run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
            run(["git", "commit", "-m", "Initial R3 robot project"])
        create = ["gh", "repo", "create", name, "--private" if private else "--public", "--source", str(directory),
                  "--remote", "origin", "--push"]
        if description:
            create += ["--description", description]
        result = run(create)
    except FileNotFoundError as error:
        return {"ok": False, "code": "tool_missing", "message": f"{error.filename} is not installed", "commands": commands}
    except subprocess.CalledProcessError as error:
        return {"ok": False, "code": "command_failed", "command": error.cmd, "returncode": error.returncode,
                "stderr": (error.stderr or "")[-2000:], "commands": commands}
    url = next((line.strip() for line in reversed((result.stdout or "").splitlines()) if line.strip().startswith("http")), "")
    return {"ok": True, "url": url, "private": private, "commands": commands}
