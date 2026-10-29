"""Sionna RT propagation backend (NVIDIA Sionna RT, Mitsuba 3 / Dr.Jit), headless on CPU.

The scene (``radio_scene.RadioScene``) is written as a Mitsuba XML file with
PLY meshes under ``build/sim/radio/scene/`` and loaded with
``sionna.rt.load_scene``. The default variant is ``llvm_ad_mono_polarized``
(Dr.Jit LLVM backend: CPU, no GPU, no display); ``R3_SIONNA_VARIANT`` overrides
it, for example ``cuda_ad_mono_polarized`` on a GPU worker.

Link channel (per packet):

* ``PathSolver`` traces specular reflection (depth 3), refraction and UTD wedge
  diffraction from the handset (Sionna transmitter) to the car antenna
  (Sionna receiver).
* The car body is part of the scene and is posed at every trace. Every
  candidate path is tested for visibility against it, so body **blockage** is
  exact. Near-field scattering by the body is *not* modelled. The antenna sits
  about 0.65 lambda above the Pi/HAT stack, where ray optics and UTD do not
  apply. Tracing reciprocally from the car antenna does sample those edges,
  but the diffracted sum moved by +-8 dB at 300 m between 0.4M, 1M and 2M rays
  per source (non-convergent). An installed-antenna pattern from a full-wave
  solver would be the right input for that (see the README).
* Traces are cached on a position grid (``rt_grid_m``, default 0.25 m, i.e. 20
  traces/s at 5 m/s) and a heading bucket (15 deg) for the body pose. The car
  body is moved to the bucket pose before each trace.
* Between traces each path is extrapolated from the cached cell center o to
  the actual antenna position p. The first path segment is recomputed exactly
  from the first interaction point v1, and the rest of the path is fixed.
  This is a first-order Fermat (stationary path) approximation:
  L'(p) = |v1 - p| + (L - |v1 - o|), a' = a L / L'.
* Narrowband channel at the FHSS hop frequency f:
  H(f) = sum_i a_i exp(-j 2 pi f L_i / c). ``a_i`` are Sionna's passband path
  coefficients (antenna patterns, Fresnel/slab reflection, UTD diffraction,
  spreading) **without** the propagation phase. Path lengths L_i are recomputed
  in float64 from the path vertices: Sionna's float32 delays (about 0.5 ps
  resolution at 5 us) would corrupt the direct-vs-ground phase difference beyond a
  few hundred metres.
* Small-scale fading is the coherent multipath sum itself: fading_dB =
  10 log10(|H|^2 / sum |a_i|^2). No statistical fading is added on top.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .radio_analytic import HALF_WAVE_DIPOLE_GAIN_DBI, SPEED_OF_LIGHT, ChannelSample
from .radio_scene import CUSTOM_MATERIALS, RadioScene

DEFAULT_VARIANT = "llvm_ad_mono_polarized"
#: Body is parked here (outside the ground plane) for static-scene traces.
_PARK = np.array([1.0e5, 1.0e5, 0.0])
_PATTERN_PEAK_DBI = {"hw_dipole": HALF_WAVE_DIPOLE_GAIN_DBI, "dipole": 1.76, "iso": 0.0}


def load_sionna() -> tuple[Any, Any, Any]:
    """Import Mitsuba (LLVM CPU variant unless overridden), Dr.Jit and Sionna RT. Raises ImportError when unavailable."""
    import mitsuba as mi

    variant = os.environ.get("R3_SIONNA_VARIANT", DEFAULT_VARIANT)
    if mi.variant() != variant:
        mi.set_variant(variant)
    import drjit as dr
    import sionna.rt as rt

    if variant.startswith("llvm") and not dr.has_backend(dr.JitBackend.LLVM):
        raise ImportError("Dr.Jit LLVM backend unavailable (libLLVM not found; set DRJIT_LIBLLVM_PATH)")
    return mi, dr, rt


def sionna_status() -> tuple[bool, str]:
    try:
        _, _, rt = load_sionna()
        return True, f"sionna-rt {rt.__version__}"
    except Exception as exc:  # noqa: BLE001 - any import/runtime failure selects the fallback
        return False, f"{type(exc).__name__}: {exc}"


def _write_ply(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    import trimesh

    trimesh.Trimesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64), process=False).export(path)


def _box(center: np.ndarray, size: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import trimesh

    mesh = trimesh.creation.box(extents=np.asarray(size, dtype=float))
    mesh.apply_translation(np.asarray(center, dtype=float))
    return np.asarray(mesh.vertices), np.asarray(mesh.faces)


def _material_xml(mat_id: str, material: str, thickness: float) -> str:
    if material in CUSTOM_MATERIALS:
        eps, sigma = CUSTOM_MATERIALS[material]
        return (f'<bsdf type="radio-material" id="{mat_id}"><float name="relative_permittivity" value="{eps}"/>'
                f'<float name="conductivity" value="{sigma}"/><float name="thickness" value="{thickness}"/></bsdf>')
    return (f'<bsdf type="itu-radio-material" id="{mat_id}"><string name="type" value="{material}"/>'
            f'<float name="thickness" value="{thickness}"/></bsdf>')


def write_scene(scene: RadioScene, directory: Path) -> Path:
    """Mitsuba XML + PLY meshes for ``scene`` (ground, obstacles, car body in the robot frame at the origin)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meshes").mkdir(exist_ok=True)
    shapes: list[str] = []
    materials: dict[str, str] = {}

    def material_id(material: str, thickness: float) -> str:
        key = f"mat-{material}-{thickness:g}".replace(".", "p")
        materials.setdefault(key, _material_xml(key, material, thickness))
        return key

    e = scene.ground_half_extent
    x0, y0 = float(scene.tx_position[0]), float(scene.tx_position[1])
    ground_v = np.array([[x0 - e, y0 - e, 0.0], [x0 + e, y0 - e, 0.0], [x0 + e, y0 + e, 0.0], [x0 - e, y0 + e, 0.0]])
    _write_ply(directory / "meshes" / "ground.ply", ground_v, np.array([[0, 1, 2], [0, 2, 3]]))
    if scene.ground_custom is not None:
        eps, sigma = scene.ground_custom
        materials["mat-ground"] = (f'<bsdf type="radio-material" id="mat-ground"><float name="relative_permittivity" value="{eps}"/>'
                                   f'<float name="conductivity" value="{sigma}"/><float name="thickness" value="10.0"/></bsdf>')
        ground_mat = "mat-ground"
    else:
        # 10 m slab: at 2.4 GHz moist soil attenuates > 60 dB/m, so the ITU slab model reduces to a half-space.
        ground_mat = material_id(scene.ground_material, 10.0)
    shapes.append(f'<shape type="ply" id="mesh-ground"><string name="filename" value="meshes/ground.ply"/>'
                  f'<boolean name="face_normals" value="true"/><ref id="{ground_mat}" name="bsdf"/></shape>')
    for box in scene.obstacles:
        vertices, faces = _box(box.center, box.size)
        name = f"obstacle-{box.name}"
        _write_ply(directory / "meshes" / f"{name}.ply", vertices, faces)
        mat = material_id(box.material, float(min(box.size)))
        shapes.append(f'<shape type="ply" id="mesh-{name}"><string name="filename" value="meshes/{name}.ply"/>'
                      f'<boolean name="face_normals" value="true"/><ref id="{mat}" name="bsdf"/></shape>')
    for body in scene.body:
        name = f"car-{body.material}"
        _write_ply(directory / "meshes" / f"{name}.ply", body.vertices + _PARK, body.faces)
        mat = material_id(body.material, 0.003)
        shapes.append(f'<shape type="ply" id="mesh-{name}"><string name="filename" value="meshes/{name}.ply"/>'
                      f'<boolean name="face_normals" value="true"/><ref id="{mat}" name="bsdf"/></shape>')
    xml = "<scene version=\"2.1.0\">\n" + "\n".join(materials.values()) + "\n" + "\n".join(shapes) + "\n</scene>\n"
    path = directory / "scene.xml"
    path.write_text(xml)
    return path


@dataclass
class PathSet:
    """Paths traced from one car-antenna position ``origin`` to the handset."""

    origin: np.ndarray
    a: np.ndarray  # complex passband coefficients, no propagation phase
    length: np.ndarray  # float64 total path length (m)
    first: np.ndarray  # (n, 3) interaction point adjacent to the car antenna (handset for LoS)
    los: bool
    diffracted: int
    refracted: int


def _yaw(rotation: np.ndarray | None) -> float:
    if rotation is None:
        return 0.0
    return math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))


def _rz(yaw: float) -> np.ndarray:
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


class SionnaPropagation:
    """Ray-traced handset <-> car-antenna channel (see module docstring)."""

    name = "sionna_rt"
    label = "Sionna RT"

    def __init__(self, scene: RadioScene, directory: Path, grid_m: float = 0.25, heading_bucket_deg: float = 15.0,
                 max_depth: int = 3, samples_per_src: int = 200_000, diffraction: bool = True, refraction: bool = True) -> None:
        mi, dr, rt = load_sionna()
        self.mi, self.dr, self.rt = mi, dr, rt
        self.scene_spec = scene
        self.tx_position = np.asarray(scene.tx_position, dtype=float)
        self.frequency = float(scene.frequency)
        self.grid = float(grid_m)
        self.grid_z = 0.01
        self.heading_bucket = math.radians(heading_bucket_deg)
        self.max_depth = int(max_depth)
        self.samples = int(samples_per_src)
        self.diffraction = bool(diffraction)
        self.refraction = bool(refraction)
        self.directory = Path(directory)
        self.xml_path = write_scene(scene, self.directory)
        self.scene = rt.load_scene(str(self.xml_path), merge_shapes=True, merge_shapes_exclude_regex="car-.*")
        self.scene.frequency = self.frequency
        self.scene.tx_array = rt.PlanarArray(num_rows=1, num_cols=1, pattern=scene.tx_pattern, polarization=scene.polarization)
        self.scene.rx_array = rt.PlanarArray(num_rows=1, num_cols=1, pattern=scene.rx_pattern, polarization=scene.polarization)
        self.handset = rt.Transmitter("handset", position=mi.Point3f(*map(float, self.tx_position)), power_dbm=float(scene.tx_power_dbm))
        self.car = rt.Receiver("car_antenna", position=mi.Point3f(*map(float, self.tx_position + [1.0, 0.0, 0.0])))
        self.scene.add(self.handset)
        self.scene.add(self.car)
        self.solver = rt.PathSolver(deterministic=True)
        self._body: list[tuple[str, np.ndarray]] = []
        for body in scene.body:
            obj = self.scene.get(f"car-{body.material}")
            if obj is not None:
                self._body.append((obj.mi_mesh.id() + ".vertex_positions", np.asarray(body.vertices, dtype=float)))
        self._body_pose: tuple[float, ...] | None = None
        self._park_body()
        # Antenna peak-gain correction: catalog peak gain minus the Sionna pattern's own peak gain.
        self.gain_correction_db = ((scene.tx_gain_dbi - _PATTERN_PEAK_DBI.get(scene.tx_pattern, 0.0))
                                   + (scene.rx_gain_dbi - _PATTERN_PEAK_DBI.get(scene.rx_pattern, 0.0)))
        self.cache: dict[tuple[int, int, int, int], PathSet] = {}
        self.traces = 0
        self.trace_time = 0.0
        self.grid_points = 0
        self.grid_time = 0.0
        self.max_paths = 0

    # ------------------------------------------------------------------ scene state
    def _set_body(self, pose: tuple[float, ...] | None, position: np.ndarray, rotation: np.ndarray) -> None:
        if not self._body or pose == self._body_pose:
            return
        params = self.scene.mi_scene_params
        for key, local in self._body:
            world = local @ rotation.T + position
            params[key] = self.mi.Float(world.astype(np.float32).ravel())
        params.update()
        self._body_pose = pose

    def _park_body(self) -> None:
        self._set_body(("parked",), _PARK, np.eye(3))

    # ------------------------------------------------------------------ tracing
    def _solve(self, points: np.ndarray, grid: bool = False) -> list[PathSet]:
        """One PathSolver call from the handset to car-antenna ``points`` (one Sionna receiver each); one PathSet per point.
        ``grid`` only selects the statistics bucket (static-scene evaluation vs link traces)."""
        mi, rt = self.mi, self.rt
        points = np.atleast_2d(np.asarray(points, dtype=float))
        added: list[str] = []
        if len(points) == 1:
            self.car.position = mi.Point3f(*map(float, points[0]))
        else:
            self.scene.remove("car_antenna")
            for i, p in enumerate(points):
                self.scene.add(rt.Receiver(f"grid_rx{i}", position=mi.Point3f(*map(float, p))))
                added.append(f"grid_rx{i}")
        t0 = time.perf_counter()
        try:
            paths = self.solver(self.scene, max_depth=self.max_depth, samples_per_src=self.samples, max_num_paths_per_src=100_000,
                                los=True, specular_reflection=True, diffuse_reflection=False, refraction=self.refraction,
                                diffraction=self.diffraction)
            a_re = np.asarray(paths.a[0], dtype=np.float64)
            a_im = np.asarray(paths.a[1], dtype=np.float64)
            tau = np.asarray(paths.tau, dtype=np.float64)
            valid = np.asarray(paths.valid)
            inter = np.asarray(paths.interactions)
            verts = np.asarray(paths.vertices, dtype=np.float64)
        finally:
            if added:
                for name in added:
                    self.scene.remove(name)
                self.scene.add(self.car)
        elapsed = time.perf_counter() - t0
        if grid:
            self.grid_points += len(points)
            self.grid_time += elapsed
        else:
            self.traces += len(points)
            self.trace_time += elapsed
        n = len(points)
        n_paths = a_re.shape[-1]
        # Tensors are [num_rx, (rx_ant), num_tx, (tx_ant), paths] with one transmitter and single-element arrays.
        a = (a_re + 1j * a_im).reshape(n, n_paths)
        tau = tau.reshape(n, n_paths)
        valid = valid.reshape(n, n_paths).astype(bool)
        depth = inter.shape[0] if inter.ndim and inter.size else 0
        inter = inter.reshape(depth, n, n_paths) if depth else np.zeros((0, n, n_paths), dtype=np.uint32)
        verts = verts.reshape(depth, n, n_paths, 3) if depth else np.zeros((0, n, n_paths, 3))
        out = []
        for s in range(n):
            idx = np.nonzero(valid[s] & (np.abs(a[s]) > 0.0))[0]
            lengths, lasts = [], []
            los = False
            n_diff = n_ref = 0
            for i in idx:
                chain = []
                kinds = 0
                for d in range(depth):
                    kind = int(inter[d, s, i])
                    if kind == 0:
                        break
                    kinds |= kind
                    chain.append(verts[d, s, i])
                ordered = [self.tx_position, *chain, points[s]]  # vertices run from the transmitter to the receiver
                length = float(sum(np.linalg.norm(ordered[j + 1] - ordered[j]) for j in range(len(ordered) - 1)))
                if abs(length - SPEED_OF_LIGHT * tau[s, i]) > 0.05:  # vertex order/precision guard
                    length = SPEED_OF_LIGHT * tau[s, i]
                lengths.append(length)
                lasts.append(ordered[-2])  # interaction (or handset) adjacent to the car antenna
                los |= kinds == 0
                n_diff += bool(kinds & 8)
                n_ref += bool(kinds & 4)
            self.max_paths = max(self.max_paths, len(idx))
            out.append(PathSet(points[s].copy(), a[s, idx], np.asarray(lengths), np.asarray(lasts).reshape(-1, 3), los, n_diff, n_ref))
        return out

    def _key(self, position: np.ndarray, yaw: float) -> tuple[int, int, int, int]:
        bucket = int(round(yaw / self.heading_bucket)) if self._body else 0
        return (int(round(position[0] / self.grid)), int(round(position[1] / self.grid)), int(round(position[2] / self.grid_z)), bucket)

    def _paths_at(self, position: np.ndarray, rotation: np.ndarray | None) -> PathSet:
        yaw = _yaw(rotation)
        key = self._key(position, yaw)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        center = np.array([key[0] * self.grid, key[1] * self.grid, key[2] * self.grid_z])
        if self._body:
            yaw_b = key[3] * self.heading_bucket
            r = _rz(yaw_b)
            self._set_body(("pose", key[3], key[0], key[1], key[2]), center - r @ self.scene_spec.antenna_offset, r)
        paths = self._solve(center[None, :])[0]
        self.cache[key] = paths
        return paths

    # ------------------------------------------------------------------ channel
    @staticmethod
    def _coherent(paths: PathSet, position: np.ndarray, frequency: float) -> tuple[complex, float]:
        if not len(paths.a):
            return 0j, 0.0
        o = paths.origin
        first_old = np.linalg.norm(paths.first - o, axis=1)
        first_new = np.linalg.norm(paths.first - position, axis=1)
        length = paths.length - first_old + first_new
        a = paths.a * (paths.length / length)
        h = complex(np.sum(a * np.exp(-2j * np.pi * frequency * length / SPEED_OF_LIGHT)))
        return h, float(np.sum(np.abs(a) ** 2))

    def channel(self, position: np.ndarray, rotation: np.ndarray | None = None, frequency: float | None = None) -> ChannelSample:
        position = np.asarray(position, dtype=float)
        paths = self._paths_at(position, rotation)
        h, incoherent = self._coherent(paths, position, self.frequency if frequency is None else frequency)
        gain = 10.0 * math.log10(max(abs(h) ** 2, 1e-30)) + self.gain_correction_db
        mean = 10.0 * math.log10(max(incoherent, 1e-30)) + self.gain_correction_db
        return ChannelSample(gain, mean, 0.0, len(paths.a), paths.los)

    def static_gain_db(self, points: np.ndarray, frequency: float | None = None, batch: int = 256) -> tuple[np.ndarray, np.ndarray]:
        """Coherent and incoherent gain (antennas included) at car-antenna ``points`` with no car body in the scene."""
        self._park_body()
        points = np.atleast_2d(np.asarray(points, dtype=float))
        f = self.frequency if frequency is None else frequency
        coherent, mean = [], []
        for start in range(0, len(points), batch):
            for paths in self._solve(points[start:start + batch], grid=True):
                h, inc = self._coherent(paths, paths.origin, f)
                coherent.append(10.0 * math.log10(max(abs(h) ** 2, 1e-30)) + self.gain_correction_db)
                mean.append(10.0 * math.log10(max(inc, 1e-30)) + self.gain_correction_db)
        self._body_pose = None  # force the next body placement
        return np.asarray(coherent), np.asarray(mean)

    def radio_map(self, center: np.ndarray, size: tuple[float, float], cell: float, height: float,
                  samples: int = 10_000_000) -> dict[str, Any]:
        """Sionna ``RadioMapSolver`` coverage (handset as transmitter, isotropic receiver, cell-averaged path gain)."""
        mi, rt = self.mi, self.rt
        self._park_body()
        self.scene.remove("car_antenna")
        try:
            t0 = time.perf_counter()
            rmap = rt.RadioMapSolver()(self.scene, center=mi.Point3f(float(center[0]), float(center[1]), float(height)),
                                       orientation=mi.Point3f(0.0, 0.0, 0.0), size=mi.Point2f(float(size[0]), float(size[1])),
                                       cell_size=mi.Point2f(cell, cell), samples_per_tx=int(samples), max_depth=self.max_depth,
                                       refraction=self.refraction, diffraction=self.diffraction)
            gain = np.asarray(rmap.path_gain)[0]
            elapsed = time.perf_counter() - t0
        finally:
            self.scene.add(self.car)
            self._body_pose = None
        # Sionna planar maps are indexed [y, x]; cell centers:
        ny, nx = gain.shape
        xs = center[0] - size[0] / 2.0 + cell * (np.arange(nx) + 0.5)
        ys = center[1] - size[1] / 2.0 + cell * (np.arange(ny) + 0.5)
        with np.errstate(divide="ignore"):
            # Isotropic map receiver -> add the catalog car-antenna peak gain; scale the handset pattern to its catalog gain.
            gain_db = (10.0 * np.log10(gain) + self.scene_spec.rx_gain_dbi
                       + (self.scene_spec.tx_gain_dbi - _PATTERN_PEAK_DBI.get(self.scene_spec.tx_pattern, 0.0)))
        return {"x": xs, "y": ys, "gain_db": gain_db, "seconds": elapsed, "cell": cell, "height": height}

    def stats(self) -> dict[str, Any]:
        return {"backend": self.name, "traces": self.traces, "trace_time_s": round(self.trace_time, 2), "cache_cells": len(self.cache),
                "max_paths": self.max_paths, "grid_m": self.grid,
                "static_points": self.grid_points, "static_time_s": round(self.grid_time, 2)}

    def assumptions(self) -> list[str]:
        s = self.scene_spec
        ground = (f"custom ground eps_r {s.ground_custom[0]}, sigma {s.ground_custom[1]} S/m" if s.ground_custom
                  else f"ITU-R P.2040 '{s.ground_material}'")
        obstacles = ", ".join(f"{b.name}: {b.material}{' (material estimated)' if b.material_estimated else ''}" for b in s.obstacles)
        body = "; ".join(f"{m.material}: {len(m.parts)} parts" for m in s.body) or "none"
        return [
            f"Sionna RT ray tracing ({self.rt.__version__}, Mitsuba variant {self.mi.variant()}): specular reflection depth "
            f"{self.max_depth}, refraction {self.refraction}, UTD wedge diffraction {self.diffraction}, {self.samples} rays/source, "
            "no diffuse scattering (ground roughness not modelled).",
            f"Scene: {ground} ground plane ({2 * s.ground_half_extent:.0f} m square), obstacles [{obstacles or 'none'}], "
            f"car body by material [{body}] (material classes are estimates). Antennas: {s.tx_pattern}/{s.rx_pattern}, "
            f"{s.polarization}-polarized, peak gains scaled to the catalog values ({self.gain_correction_db:+.2f} dB total). "
            f"Car antenna offset {np.round(s.antenna_offset, 3).tolist()} m ({s.antenna_offset_source}).",
            f"Traced handset -> car antenna on a {self.grid} m grid x {math.degrees(self.heading_bucket):.0f} deg heading "
            "buckets with the car body posed per trace (exact blockage; near-field body scattering not modelled); first-segment exact extrapolation between traces; coherent sum at the "
            "FHSS hop frequency gives small-scale fading (no statistical fading model).",
        ] + list(s.notes)
