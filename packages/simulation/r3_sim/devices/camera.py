"""Virtual CSI camera: MuJoCo offscreen renders of the twin at the catalog
intrinsics and mount pose, delivered with libcamera-like timing.

Timing model (lockstep, deterministic):

* after ``camera.configure`` at time ``t_c`` the sensor starts frame ``k`` at
  ``t_k = t_c + startup + k / fps`` (exposure start, the frame timestamp);
* the kernel snapshots the vehicle state (``qpos``) when a step crosses ``t_k``;
* frame ``k`` is complete at ``t_k + 1 / fps`` (rolling readout of one frame
  period) ``+ isp_latency``;
* ``camera.capture`` returns the newest completed frame not returned yet (a
  one-deep completed queue as in picamera2: older ones are dropped and show as
  gaps in ``sequence``), or blocks until the next one completes.

The image is rendered from the snapshot into a private ``MjData`` (forward
kinematics only), so rendering never touches the simulation state and runs are
bit-identical with or without cameras. Read noise is Gaussian per pixel with
``read_noise_dn`` (estimate), from the camera's own random stream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.units import to_si

FORMATS = {"rgb888": 3, "gray8": 1}
DEFAULT_ISP_LATENCY = 0.008   # s, libcamera ISP + buffer handoff on a Pi 5 (estimate)
DEFAULT_STARTUP = 0.1         # s from configure to the first exposure (estimate)
DEFAULT_READ_NOISE_DN = 2.0   # 8-bit DN, sigma (estimate)


class CameraError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class CameraMode:
    width: int
    height: int
    fps: float
    format: str


@dataclass
class VirtualCamera:
    component: str
    port: str
    camera_name: str
    params: dict[str, Any]
    model: Any  # mujoco.MjModel
    rng: np.random.Generator
    mode: CameraMode | None = None
    configured_at: float = 0.0
    next_index: int = 0
    snapshots: dict[int, tuple[float, np.ndarray]] = field(default_factory=dict)
    returned: int = -1
    frames_rendered: int = 0
    frames_dropped: int = 0
    _renderer: Any = None
    _scratch: Any = None
    _renderer_size: tuple[int, int] = (0, 0)

    # ------------------------------------------------------------------ configuration
    def modes(self) -> list[dict[str, Any]]:
        return [dict(m) for m in self.params.get("modes", [])]

    def configure(self, now: float, width: int, height: int, fps: float, format: str) -> dict[str, Any]:
        import mujoco

        if format not in FORMATS:
            raise CameraError("bad_format", f"format {format!r}; supported: {', '.join(FORMATS)}")
        modes = self.modes()
        fitting = [m for m in modes if int(m["width"]) >= width and int(m["height"]) >= height]
        if modes and not fitting:
            listed = ", ".join(f"{m['width']}x{m['height']}" for m in modes)
            raise CameraError("mode_unsupported", f"{width}x{height} exceeds every sensor mode ({listed})")
        max_fps = min((float(m["fps"]) for m in fitting), default=float(self.params.get("max_fps", 30.0)))
        # The fastest mode that covers the request sets the frame-rate limit (libcamera picks the sensor mode).
        max_fps = max((float(m["fps"]) for m in fitting), default=max_fps)
        if fps <= 0 or fps > max_fps + 1e-9:
            raise CameraError("mode_unsupported", f"{fps:g} fps at {width}x{height}: sensor modes allow up to {max_fps:g} fps")
        offscreen = (int(self.model.vis.global_.offwidth), int(self.model.vis.global_.offheight))
        if width > offscreen[0] or height > offscreen[1]:
            raise CameraError("mode_unsupported", f"{width}x{height} exceeds the twin's offscreen buffer {offscreen[0]}x{offscreen[1]}")
        self.mode = CameraMode(int(width), int(height), float(fps), format)
        self.configured_at = now
        self.next_index = 0
        self.snapshots.clear()
        self.returned = -1
        if self._renderer is None or self._renderer_size != (width, height):
            if self._renderer is not None:
                self._renderer.close()
            self._renderer = mujoco.Renderer(self.model, int(height), int(width))
            self._renderer_size = (width, height)
        if self._scratch is None:
            self._scratch = mujoco.MjData(self.model)
        return {"width": self.mode.width, "height": self.mode.height, "fps": self.mode.fps, "format": format,
                "intrinsics": self.intrinsics()}

    def intrinsics(self) -> dict[str, Any]:
        """Pinhole intrinsics of the configured output (pixels): MuJoCo renders a symmetric frustum
        with the vertical FOV of the camera element and square pixels."""
        assert self.mode is not None
        fovy = math.radians(float(self.model.cam_fovy[self._camera_id()]))
        fy = self.mode.height / 2 / math.tan(fovy / 2)
        return {"fx": fy, "fy": fy, "cx": self.mode.width / 2, "cy": self.mode.height / 2,
                "hfov_deg": math.degrees(2 * math.atan(self.mode.width / 2 / fy)), "vfov_deg": math.degrees(fovy),
                "distortion": [0.0, 0.0, 0.0, 0.0, 0.0]}

    def _camera_id(self) -> int:
        import mujoco

        return int(mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, self.camera_name))

    # ------------------------------------------------------------------ timing
    @property
    def period(self) -> float:
        assert self.mode is not None
        return 1.0 / self.mode.fps

    def exposure_time(self, index: int) -> float:
        return self.configured_at + float(self.params.get("startup", DEFAULT_STARTUP)) + index * self.period

    def completion_time(self, index: int) -> float:
        return self.exposure_time(index) + self.period + float(self.params.get("isp_latency", DEFAULT_ISP_LATENCY))

    def sample(self, t: float, state: Any) -> None:
        """Called by the kernel after every step: snapshot the state at each exposure start it crosses."""
        if self.mode is None:
            return
        while self.exposure_time(self.next_index) <= t + 1e-9:
            self.snapshots[self.next_index] = (self.exposure_time(self.next_index), state.qpos())
            self.next_index += 1
            # Keep only frames that can still be returned (newest completed + those in flight).
            for old in [k for k in self.snapshots if k < self.next_index - 8]:
                self.snapshots.pop(old)

    def ready_index(self, now: float) -> int | None:
        """Newest completed frame not returned yet."""
        done = [k for k in self.snapshots if k > self.returned and self.completion_time(k) <= now + 1e-9]
        return max(done) if done else None

    def next_completion(self) -> float:
        return self.completion_time(max(self.returned + 1, 0))

    # ------------------------------------------------------------------ rendering
    def take(self, index: int) -> dict[str, Any]:
        import mujoco

        assert self.mode is not None
        t_exposure, qpos = self.snapshots[index]
        self.frames_dropped += max(0, index - self.returned - 1) if self.returned >= 0 else index
        self.returned = index
        scratch = self._scratch
        scratch.qpos[:] = qpos
        mujoco.mj_kinematics(self.model, scratch)
        mujoco.mj_camlight(self.model, scratch)
        self._renderer.update_scene(scratch, camera=self.camera_name)
        image = self._renderer.render()
        noise = float(self.params.get("read_noise_dn", DEFAULT_READ_NOISE_DN))
        if noise > 0:
            image = np.clip(image.astype(np.float32) + self.rng.normal(0.0, noise, image.shape), 0, 255).astype(np.uint8)
        if self.mode.format == "gray8":
            image = (image[..., 0] * 0.299 + image[..., 1] * 0.587 + image[..., 2] * 0.114).astype(np.uint8)
        self.frames_rendered += 1
        return {"frame_t": round(t_exposure, 9), "sequence": index, "width": self.mode.width, "height": self.mode.height,
                "format": self.mode.format, "exposure_s": float(to_si(self.params.get("exposure", self.period * 0.5))),
                "image": np.ascontiguousarray(image)}

    def close(self) -> None:
        self.mode = None
        self.snapshots.clear()
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None


def cameras_for(project: Any, computer: str, model: Any, seed: int) -> tuple[dict[str, VirtualCamera], list[str]]:
    """Virtual cameras per CSI port of ``computer`` (``csi`` bindings), and problems found.

    A port is served only when its binding pins electrically reach a camera
    component (catalog behaviour ``camera.*``) through connectors and cables."""
    from ..vehicle.scene_visual import camera_components, camera_name, camera_params

    cameras: dict[str, VirtualCamera] = {}
    problems: list[str] = []
    graph = project.graph
    candidates = camera_components(project)
    for name, binding in project.spec.software.bindings.items():
        if binding.interface != "csi" or binding.computer != computer:
            continue
        port = binding.device or "cam0"
        reached = []
        for cid in candidates:
            ctype = project.catalog.component(project.spec.components[cid].type)
            camera_pins = [f"C:{cid}.{pin}" for pin in ctype.pins]
            for ref in binding.pins:
                try:
                    node = graph._pin_node(ref)
                except KeyError:
                    continue
                if any(graph.connected(node, target, through_internal=True) for target in camera_pins):
                    reached.append(cid)
                    break
        if not reached:
            problems.append(f"csi binding {name} ({port}) does not electrically reach a camera")
            continue
        cid = reached[0]
        stream = np.random.default_rng(np.random.SeedSequence([int(seed), 0xCA4E, len(cameras)]))
        cameras[port] = VirtualCamera(cid, port, camera_name(cid), camera_params(project, cid) or {}, model, stream)
    return cameras, problems
