"""Explicit, source-aware robot description files.

A value ``{$include: parts/link.yaml}`` is replaced by the complete value in
that file. Paths are project-relative, confined to the project, literal (no
globs), and each file has exactly one owner. This is substitution, never an
implicit merge. RobotSpec remains the stage-facing contract.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import yaml
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.constructor import RoundTripConstructor
from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.scalarfloat import ScalarFloat

from .spec import RobotSpec

if TYPE_CHECKING:
    from .pulled import PulledModel

SpecPath = tuple[str | int, ...]


class SpecError(ValueError):
    """Stable, transport-independent errors, with physical and logical paths."""

    def __init__(self, code: str, file: str, path: SpecPath, message: str) -> None:
        self.code = code
        self.errors = [{"code": code, "file": file, "path": list(path),
                        "loc": ".".join(map(str, path)), "msg": message, "type": code}]
        super().__init__(f"{file}:{'.'.join(map(str, path)) or '$'}: {message}")

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": str(self), "errors": self.errors}


@dataclass(frozen=True)
class Origin:
    file: str
    path: SpecPath


@dataclass
class SpecFiles:
    root: Path
    data: Any = None
    texts: dict[str, str] = field(default_factory=dict)
    origins: dict[SpecPath, Origin] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        # Preserve the existing revision for single-file projects.
        if set(self.texts) == {"robot.yaml"}:
            return hashlib.sha256(self.texts["robot.yaml"].encode()).hexdigest()
        digest = hashlib.sha256()
        for name, text in sorted(self.texts.items()):
            for value in (name.encode(), text.encode()):
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
        return digest.hexdigest()

    def origin(self, path: SpecPath) -> Origin:
        requested = path
        while path not in self.origins:
            path = path[:-1]
        origin = self.origins[path]
        return Origin(origin.file, (*origin.path, *requested[len(path):]))

    def error(self, code: str, path: SpecPath, message: str) -> SpecError:
        source = self.origin(path)
        error = SpecError(code, source.file, path, message)
        error.errors[0]["source_path"] = list(source.path)
        return error

    def validate(self) -> RobotSpec:
        _check_definition_ids(self)
        try:
            spec = RobotSpec.model_validate(self.data)
        except ValidationError as error:
            errors = []
            for item in error.errors(include_url=False):
                code = "spec_unknown_key" if item["type"] == "extra_forbidden" else "spec_invalid"
                message = item["msg"]
                if "unknown" in message or "not a component" in message:
                    code = "spec_reference_undefined"
                if "unique" in message or "must differ" in message:
                    code = "spec_duplicate_definition"
                path = tuple(item.get("ctx", {}).get("spec_path", item["loc"]))
                # Existing model-level validators include the offending identity
                # in the message. Locate it rather than losing the source file.
                if not path:
                    candidates = [p for p, v in _leaves(self.data) if isinstance(v, str) and repr(v) in message]
                    path = max(candidates, key=len, default=())
                errors.extend(self.error(code, path, message).errors)
            failure = SpecError(errors[0]["code"], errors[0]["file"], tuple(errors[0]["path"]), errors[0]["msg"])
            failure.errors = errors
            raise failure from error
        _check_local_references(self, spec)
        return spec


def _leaves(value: Any, path: SpecPath = ()) -> Iterator[tuple[SpecPath, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, (*path, index))
    else:
        yield path, value


class _DescriptionLoader(yaml.SafeLoader):
    """Accept the scientific floats emitted by the YAML 1.2 round-trip writer."""


_DescriptionLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^[-+]?(?:[0-9][0-9_]*)(?:\.[0-9_]*)?[eE][-+]?[0-9]+$"),
    list("-+0123456789"),
)


def _parse(text: str, file: str) -> Any:
    """Reject duplicate YAML keys before a YAML parser can overwrite them."""
    loader = _DescriptionLoader(text)
    try:
        node = loader.get_single_node()

        def read(current: Any, path: SpecPath, ancestors: set[int]) -> Any:
            if current is None:
                return None
            if id(current) in ancestors:
                raise SpecError("spec_include_cycle", file, path, "recursive YAML alias")
            ancestors = ancestors | {id(current)}
            if isinstance(current, yaml.MappingNode):
                result = {}
                for key_node, value_node in current.value:
                    key = loader.construct_object(key_node, deep=True)
                    if not isinstance(key, str):
                        raise SpecError("spec_invalid", file, path, "mapping keys must be strings")
                    if key in result:
                        raise SpecError("spec_duplicate_definition", file, (*path, key), f"duplicate key {key!r}")
                    result[key] = read(value_node, (*path, key), ancestors)
                return result
            if isinstance(current, yaml.SequenceNode):
                return [read(child, (*path, i), ancestors) for i, child in enumerate(current.value)]
            return loader.construct_object(current, deep=True)

        return read(node, (), set())
    except yaml.YAMLError as error:
        raise SpecError("spec_yaml_invalid", file, (), str(error)) from error
    finally:
        loader.dispose()


def load_spec_files(root: str | Path, overrides: dict[str, str] | None = None) -> SpecFiles:
    """Resolve includes and retain physical ownership; callers lock transactions.

    ``overrides`` validates proposed file bytes without touching the filesystem.
    No schema defaults or sourced/catalog values are written back to sources.
    """
    result = SpecFiles(Path(root).resolve())

    def read_file(name: str, target: SpecPath, stack: tuple[str, ...], declared: Origin) -> Any:
        path = result.root / name
        if ".." in Path(name).parts or str(Path(name)) != name:
            raise SpecError("spec_include_invalid", declared.file, declared.path, "include must use a normalized project-relative path")
        if Path(name).is_absolute() or result.root not in path.resolve().parents:
            raise SpecError("spec_include_outside_project", declared.file, declared.path, f"include escapes project: {name}")
        name = path.relative_to(result.root).as_posix()
        if name in stack:
            raise SpecError("spec_include_cycle", declared.file, declared.path, f"include cycle: {' -> '.join((*stack, name))}")
        if name in result.texts or any(path.resolve() == (result.root / previous).resolve() for previous in result.texts):
            raise SpecError("spec_duplicate_definition", declared.file, declared.path, f"file already included: {name}")
        try:
            text = overrides[name] if overrides is not None and name in overrides else path.read_bytes().decode("utf-8")
        except FileNotFoundError as error:
            raise SpecError("spec_include_missing", declared.file, declared.path, f"missing file: {name}") from error
        except OSError as error:
            raise SpecError("spec_file_unreadable", name, (), str(error)) from error
        result.texts[name] = text
        return expand(_parse(text, name), name, (), target, (*stack, name))

    def expand(value: Any, name: str, source: SpecPath, target: SpecPath, stack: tuple[str, ...]) -> Any:
        result.origins[target] = Origin(name, source)
        if isinstance(value, dict) and "$include" in value:
            if set(value) != {"$include"} or not isinstance(value["$include"], str) or not value["$include"]:
                raise SpecError("spec_include_invalid", name, source, "$include must be the only key and name one file")
            return read_file(value["$include"], target, stack, Origin(name, (*source, "$include")))
        if isinstance(value, dict):
            return {key: expand(child, name, (*source, key), (*target, key), stack) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child, name, (*source, i), (*target, i), stack) for i, child in enumerate(value)]
        return value

    result.data = read_file("robot.yaml", (), (), Origin("robot.yaml", ()))
    return result


@contextlib.contextmanager
def spec_lock(root: str | Path, shared: bool = False) -> Iterator[None]:
    """One lock for the complete include graph, shared by readers and writers."""
    with open(Path(root) / ".robot.yaml.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class _LiteralFloatConstructor(RoundTripConstructor):
    def construct_yaml_float(self, node: Any) -> Any:
        value = super().construct_yaml_float(node)
        if isinstance(value, ScalarFloat):
            value._r3_literal = (float(value), node.value)
        return value


class _LiteralFloatRepresenter(RoundTripRepresenter):
    def represent_scalar_float(self, value: ScalarFloat) -> Any:
        original = getattr(value, "_r3_literal", None)
        if original is not None and float(value).hex() == original[0].hex():
            # The default emitter can rewrite a long mantissa even when no
            # edit touched it. Preserve native evidence byte-for-byte.
            return self.represent_scalar("tag:yaml.org,2002:float", original[1],
                                         anchor=value.yaml_anchor(any=True))
        return super().represent_scalar_float(value)


_LiteralFloatConstructor.add_constructor("tag:yaml.org,2002:float",
                                        _LiteralFloatConstructor.construct_yaml_float)
for _float_type in (ScalarFloat, *ScalarFloat.__subclasses__()):
    _LiteralFloatRepresenter.add_representer(_float_type, _LiteralFloatRepresenter.represent_scalar_float)


def roundtrip(text: str) -> tuple[YAML, Any]:
    loader = YAML()
    loader.Constructor = _LiteralFloatConstructor
    loader.Representer = _LiteralFloatRepresenter
    loader.preserve_quotes = True
    loader.width = 4096
    loader.indent(mapping=2, sequence=4, offset=2)
    return loader, loader.load(text)


def dump_yaml(loader: YAML, value: Any) -> str:
    stream = io.StringIO()
    loader.dump(value, stream)
    return stream.getvalue()


def atomic_write_files(root: Path, updates: dict[str, str]) -> None:
    """Stage every file before replacing any; roll back on a failed replace.

    Readers using spec_lock see the complete old or new description. Individual
    renames are atomic; a power loss across multiple renames is not transactional.
    """
    pending: dict[str, str] = {}
    originals: dict[str, bytes | None] = {}
    replaced: list[str] = []
    try:
        for name, text in updates.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[name] = path.read_bytes() if path.exists() else None
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
            pending[name] = temporary
            with os.fdopen(handle, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        for name, temporary in pending.items():
            os.replace(temporary, root / name)
            replaced.append(name)
    except BaseException:
        for name in reversed(replaced):
            original = originals[name]
            if original is None:
                (root / name).unlink()
            else:
                handle, temporary = tempfile.mkstemp(dir=(root / name).parent)
                with os.fdopen(handle, "wb") as stream:
                    stream.write(original)
                os.chmod(temporary, (root / name).stat().st_mode & 0o777)
                os.replace(temporary, root / name)
        raise
    finally:
        for temporary in pending.values():
            Path(temporary).unlink(missing_ok=True)


def _check_definition_ids(files: SpecFiles) -> None:
    """Identity-bearing schema lists must not silently define an object twice."""
    if not isinstance(files.data, dict):
        return
    groups = (("mechanical", "joints", "id"), ("mechanical", "transmissions", "id"),
              ("mechanical", "fasteners", "id"), ("mechanical", "interfaces", "id"),
              ("electrical", "harnesses", "id"), ("software", "modules", "name"),
              ("simulation", "scenarios", "id"), ("fabrication", "load_cases", "id"),
              ("pcb", "boards", "source"), ("attachments", "id"))
    for *path, identity in groups:
        values = files.data
        for key in path:
            values = values.get(key, {}) if isinstance(values, dict) else {}
        if not isinstance(values, list):
            continue
        seen: set[str] = set()
        for index, item in enumerate(values):
            value = item.get(identity) if isinstance(item, dict) else None
            if not isinstance(value, str):
                continue
            if value in seen:
                raise files.error("spec_duplicate_definition", (*path, index, identity), f"duplicate {identity} {value!r}")
            seen.add(value)


def _check_local_references(files: SpecFiles, spec: RobotSpec) -> None:
    """References independent of the external catalog (checked by graph stages)."""
    def require(value: str, names: Any, path: SpecPath) -> None:
        if value not in names:
            raise files.error("spec_reference_undefined", path, f"undefined reference {value!r}")

    attachment_ids: set[str] = set()
    for i, attachment in enumerate(spec.attachments):
        if attachment.id in attachment_ids:
            raise files.error("spec_duplicate_definition", ("attachments", i, "id"), f"duplicate attachment {attachment.id!r}")
        attachment_ids.add(attachment.id)
        for label in ("a", "b"):
            part, connector = getattr(attachment, label)
            require(part, spec.components, ("attachments", i, label, 0))
            pulled = spec.components[part].pulled
            require(connector, pulled.mate_connectors if pulled else {}, ("attachments", i, label, 1))
    for cid, component in spec.components.items():
        if component.purchased_unit:
            require(component.purchased_unit, spec.components, ("components", cid, "purchased_unit"))
    for name in spec.cad.parts:
        require(name, spec.components, ("cad", "parts", name))
    for group, members in spec.cad.subassemblies.items():
        for i, member in enumerate(members):
            require(member, spec.components, ("cad", "subassemblies", group, i))
    for name in spec.fabrication.parts:
        require(name, spec.components, ("fabrication", "parts", name))
    harnesses = {h.id: h for h in spec.electrical.harnesses}
    collisions = set(harnesses) & set(spec.components)
    if collisions:
        i = next(i for i, h in enumerate(spec.electrical.harnesses) if h.id in collisions)
        raise files.error("spec_duplicate_definition", ("electrical", "harnesses", i, "id"), "harness and component identities must differ")
    owners = set(spec.components) | set(harnesses)
    for i, harness in enumerate(spec.electrical.harnesses):
        for name, connector in harness.connectors.items():
            if connector.mates:
                require(connector.mates.split(".")[0], owners, ("electrical", "harnesses", i, "connectors", name, "mates"))
        for j, wire in enumerate(harness.wires):
            for label, end in (("from", wire.from_), ("to", wire.to)):
                if end not in harness.splices:
                    require(end.partition(".")[0], harness.connectors, ("electrical", "harnesses", i, "wires", j, label))
    for i, mate in enumerate(spec.electrical.direct_mates):
        for label in ("a", "b"):
            require(getattr(mate, label).partition(".")[0], owners, ("electrical", "direct_mates", i, label))
    for name, binding in spec.software.bindings.items():
        require(binding.computer, spec.components, ("software", "bindings", name, "computer"))
        for label in ("pins", "expects"):
            for i, pin in enumerate(getattr(binding, label)):
                require(pin.partition(".")[0], owners, ("software", "bindings", name, label, i))


def _pulled_values(model: PulledModel, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((root / "cad/pulled/pull.json").read_text())
    parts = {}
    for name, part in model.parts.items():
        metadata = manifest["parts"][name]
        onshape = {"document_id": model.document_id, "version_id": model.version_id,
                   "element_id": None, "part_id": None, "occurrence_path": [], "configuration": ""}
        onshape.update(metadata.get("onshape") or {})
        parts[name] = {"owner": "onshape-pull", "onshape": onshape, "document_id": model.document_id,
            "version_id": model.version_id, "sha256": part.sha256,
            "mate_connectors": {key: frame.tolist() for key, frame in part.mate_connectors.items()},
            "pose": model.poses[name].tolist(), "mass": part.mass, "com": part.com,
            "inertia": part.inertia, "material": part.material, "density": metadata.get("density"),
            "volume": metadata.get("volume"), "purchased": part.purchased}
        if part.gltf is not None:
            parts[name]["gltf"] = part.gltf.relative_to(root / "cad/pulled").as_posix()
        for kind in ('stl', 'step', 'obj'):
            path = getattr(part, kind)
            if path is not None:
                parts[name][kind] = path.relative_to(root / 'cad/pulled').as_posix()
        if part.obj is not None:
            parts[name]['geometry_qualification'] = part.geometry_qualification
    native_mates = {item["id"]: item for item in manifest["mates"]}
    attachments = [{"owner": "onshape-pull", "feature": native_mates[mate.id].get("feature"), "id": mate.id, "kind": mate.kind,
                    "a": list(mate.a), "b": list(mate.b), "axis": mate.axis, "limits": mate.limits} for mate in model.mates]
    return parts, attachments


def _check_pulled_instances(files: SpecFiles, model: PulledModel) -> None:
    from .pulled import PullError

    described = set(files.data["components"])
    missing = described - set(model.parts)
    unknown = set(model.parts) - described
    if missing:
        raise PullError("cad_pull_missing", f"Described components missing from CAD pull: {', '.join(sorted(missing))}")
    if unknown:
        raise PullError("cad_pull_invalid", f"Undeclared components in CAD pull: {', '.join(sorted(unknown))}")


def _pulled_placements(files: SpecFiles, pulled: PulledModel) -> dict[str, dict[str, Any]]:
    from .assembly import rpy_from_matrix

    preferred = (files.data.get("assembly") or {}).get("root")
    root = preferred if preferred in pulled.parts else next(iter(pulled.parts))
    parents = {name: "world" for name in pulled.parts}
    edges = {name: [] for name in pulled.parts}
    for mate in pulled.mates:
        edges[mate.a[0]].append(mate.b[0])
        edges[mate.b[0]].append(mate.a[0])
    seen = set()
    for start in [root, *pulled.parts]:
        if start in seen:
            continue
        seen.add(start)
        queue = [start]
        for parent in queue:
            for child in edges[parent]:
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
                    parents[child] = parent
    return {name: {"pose": {"xyz": pulled.poses[name][:3, 3].tolist(), "rpy": rpy_from_matrix(pulled.poses[name])},
                   "mount": parents[name]} for name in pulled.parts}


def validate_pulled_spec(root: str | Path) -> PulledModel:
    """Return verified PulledModel or raise PullError(cad_pull_modified/invalid/...).

    Checks the resolved local metadata, frames, poses and attachments against
    the immutable, checksum-pinned manifest, in addition to load_pulled's checks.
    Does not trust an editable YAML mirror as geometry authority.
    """
    from .pulled import PullError, load_pulled

    root = Path(root).resolve()
    with spec_lock(root, shared=True):
        pulled = load_pulled(root)
        try:
            files = load_spec_files(root)
            files.validate()
        except SpecError as error:
            raise PullError("cad_pull_modified", f"Local pulled description is invalid: {error}") from error
        _check_pulled_instances(files, pulled)
        expected_parts, attachments = _pulled_values(pulled, root)
        placements = _pulled_placements(files, pulled)
        for name, descriptor in files.data["components"].items():
            selection = descriptor.get("onshape") or {}
            for key in ("occurrence_path", "configuration"):
                if key in selection and selection[key] is not None and selection[key] != expected_parts[name]["onshape"][key]:
                    raise PullError("cad_pull_modified", f"components.{name}.onshape.{key} differs from the pulled instance; pull again")
            for key in ("pose", "mount"):
                if key in descriptor and descriptor[key] != placements[name][key]:
                    raise PullError("cad_pull_modified", f"components.{name}.{key} differs from the native assembly; pull again")
        actual = {name: value.get("pulled") for name, value in files.data["components"].items() if value.get("pulled") is not None}
        if actual != expected_parts or files.data.get("attachments", []) != attachments:
            raise PullError("cad_pull_modified", "Local pulled metadata, poses or attachments differ from the pinned manifest; pull again")
        return pulled


def refresh_pulled_spec(root: str | Path, expected_sha256: str | None = None,
                        *, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pull-only hook: mirror the verified immutable manifest into part files.

    Call after atomically publishing cad/pulled and pinning cad_pull in the lock.
    Manifest instances must exactly equal components; missing or extra instances
    fail before any write, preserving the previous description on failure.
    """
    from .pulled import PullError, load_pulled

    root = Path(root).resolve()
    with spec_lock(root):
        files = load_spec_files(root)
        files.validate()
        if expected_sha256 is not None and files.sha256 != expected_sha256:
            raise files.error("spec_conflict", (), "description changed since read")
        pulled = load_pulled(root)
        _check_pulled_instances(files, pulled)
        if manifest is not None and json.loads((root / "cad/pulled/pull.json").read_text()) != manifest:
            raise PullError("cad_pull_modified", "Refresh manifest differs from the pinned CAD pull")
        parts, attachments = _pulled_values(pulled, root)
        placements = _pulled_placements(files, pulled)
        documents = {name: roundtrip(text) for name, text in files.texts.items()}
        changed = set()
        for name in files.data["components"]:
            origin = files.origin(("components", name))
            document = documents[origin.file][1]
            for key in origin.path:
                document = document[key]
            document["pulled"] = parts[name]
            document.update(placements[name])
            changed.add(origin.file)
        origin = files.origin(("attachments",)) if "attachments" in files.data else Origin("robot.yaml", ("attachments",))
        document = documents[origin.file][1]
        if origin.path:
            for key in origin.path[:-1]:
                document = document[key]
            document[origin.path[-1]] = attachments
        else:
            documents[origin.file] = documents[origin.file][0], attachments
        changed.add(origin.file)
        updates = {name: dump_yaml(*documents[name]) for name in changed}
        proposed = load_spec_files(root, updates)
        proposed.validate()
        atomic_write_files(root, updates)
        return {"ok": True, "previous_sha256": files.sha256, "sha256": proposed.sha256, "files": sorted(changed)}
