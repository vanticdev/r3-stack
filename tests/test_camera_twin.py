"""Camera end to end: r3_hal camera client <-> twin HAL server <-> MuJoCo offscreen render.

Uses the printbot fixture (PETG frame, Raspberry Pi 5, Arducam IMX296 on CAM0 through a 22-22 FFC).
"""

from __future__ import annotations

import shutil
import sys
import threading
import types
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest

from r3_hal import HalError
from r3_hal.virtual import VirtualHal
from r3_model.project import Project
from r3_model.spec import Scenario
from r3_sim.hal_server import TwinHalServer
from r3_sim.kernel import CoSimKernel

FIXTURE = Path(__file__).parent / "fixtures" / "printbot"
TRACK = {"ground": "grass", "track": {"points": [[0, 0], [6, 0], [8, 3], [6, 6], [0, 6], [-2, 3]], "width": 0.9},
         "cone_spacing": 1.5}


@pytest.fixture
def printbot(tmp_path: Path) -> Path:
    root = tmp_path / "printbot"
    shutil.copytree(FIXTURE, root)
    return root


def run_client(root: Path, client: Callable[[VirtualHal], Any], visual: dict[str, Any] | None = None,
               duration: float = 3.0) -> tuple[Any, CoSimKernel, TwinHalServer]:
    """Serve one twin session in this thread while ``client`` runs against it in another."""
    project = Project.load(root)
    if visual is not None:
        project.spec.simulation.environment.visual = visual
    kernel = CoSimKernel(project, Scenario(id="camera", duration=duration))
    server = TwinHalServer(kernel)
    out: dict[str, Any] = {}

    def target() -> None:
        hal = VirtualHal(server.endpoint, module="vision")
        try:
            out["value"] = client(hal)
        except BaseException as error:  # noqa: BLE001 - re-raised in the test thread
            out["error"] = error
        finally:
            hal.close()

    thread = threading.Thread(target=target)
    thread.start()
    assert server.accept(10)
    while server.serve_one() == "ok":
        pass
    thread.join(10)
    server.close()
    if "error" in out:
        raise out["error"]
    return out.get("value"), kernel, server


def test_frames_are_paced_timestamped_and_rendered(printbot: Path) -> None:
    def client(hal: VirtualHal) -> Any:
        camera = hal.camera("cam0", 320, 240, 30.0)
        return camera.intrinsics, [camera.capture() for _ in range(5)], hal.clock.now()

    (intrinsics, frames, now), kernel, server = run_client(printbot, client, TRACK)
    assert server.errors == {}
    # Exposure starts on the frame grid after the 0.1 s startup; consecutive sequence numbers.
    assert [f.sequence for f in frames] == [0, 1, 2, 3, 4]
    np.testing.assert_allclose([f.t for f in frames], [0.1 + k / 30 for k in range(5)], atol=1e-6)
    # The last frame completes one frame period + ISP latency after its exposure; time advanced only by waiting.
    assert now == pytest.approx(frames[-1].t + 1 / 30 + 0.008, abs=1.5e-3)
    image = frames[-1].array()
    assert image.shape == (240, 320, 3) and image.dtype == np.uint8
    # Catalog vertical FOV 33 deg -> fy = (240 / 2) / tan(16.5 deg).
    assert intrinsics["vfov_deg"] == pytest.approx(33.0)
    assert intrinsics["fy"] == pytest.approx(120 / np.tan(np.radians(16.5)), rel=1e-6)
    sky, lane = image[:60].reshape(-1, 3).mean(axis=0), image[-60:, :60].reshape(-1, 3).mean(axis=0)
    centre = image[-40:, 140:180].reshape(-1, 3).mean(axis=0)
    assert sky[2] > sky[0] + 20                                  # blue sky above the horizon
    assert abs(lane[0] - lane[2]) < 15 and lane.mean() < 110     # grey asphalt beside the centre line
    assert centre[0] > 150 and centre[1] > 120 and centre[2] < 90  # the yellow dashed centre line straight ahead
    assert kernel.cameras["cam0"].frames_rendered == 5


def test_slow_software_drops_frames(printbot: Path) -> None:
    def client(hal: VirtualHal) -> Any:
        camera = hal.camera("cam0", 160, 120, 30.0)
        first = camera.capture()
        hal.clock.sleep(0.1)              # busy for three frame periods
        return first, camera.capture()

    (first, second), _, _ = run_client(printbot, client)
    assert first.sequence == 0
    assert second.sequence >= 3 and second.t == pytest.approx(0.1 + second.sequence / 30, abs=1e-6)


def test_capture_is_deterministic(printbot: Path) -> None:
    def client(hal: VirtualHal) -> bytes:
        return hal.camera("cam0", 160, 120, 20.0, format="gray8").capture().data

    a, _, _ = run_client(printbot, client, TRACK)
    b, _, _ = run_client(printbot, client, TRACK)
    assert len(a) == 160 * 120 and a == b


def test_camera_errors(printbot: Path) -> None:
    def client(hal: VirtualHal) -> list[str]:
        codes = []
        for call in (lambda: hal.camera("cam0", 640, 480, 120.0),   # beyond the 60.38 fps sensor mode
                     lambda: hal.camera("cam0", 2000, 1500, 30.0),  # beyond 1456 x 1088
                     lambda: hal.camera("cam1", 640, 480, 30.0),    # nothing wired to CAM1
                     lambda: hal.camera("cam0", 640, 480, 30.0, format="yuv420")):
            try:
                call()
            except HalError as error:
                codes.append(error.code)
        return codes

    codes, _, _ = run_client(printbot, client)
    assert codes == ["mode_unsupported", "mode_unsupported", "no_such_device", "bad_format"]


def test_unwired_camera_is_not_served(printbot: Path) -> None:
    text = (printbot / "robot.yaml").read_text().replace("    - {a: csi_cable.B, b: pi.CAM0}\n", "")
    (printbot / "robot.yaml").write_text(text)

    def client(hal: VirtualHal) -> str:
        try:
            hal.camera("cam0", 320, 240, 30.0)
        except HalError as error:
            return error.code
        return "ok"

    code, kernel, _ = run_client(printbot, client)
    assert code == "no_such_device"
    assert any(e["event"] == "camera_not_connected" for e in kernel.recorder.events)


def test_cameras_do_not_change_the_physics(printbot: Path) -> None:
    def watch(capture: bool) -> Callable[[VirtualHal], Any]:
        def client(hal: VirtualHal) -> Any:
            camera = hal.camera("cam0", 160, 120, 30.0) if capture else None
            for _ in range(10):
                if camera is not None:
                    camera.capture()
                else:
                    hal.clock.sleep(1 / 30)
            hal.clock.sleep_until(0.6)
        return client

    _, with_camera, _ = run_client(printbot, watch(True), duration=0.6)
    _, without, _ = run_client(printbot, watch(False), duration=0.6)
    assert with_camera.t == pytest.approx(without.t)
    np.testing.assert_array_equal(with_camera.vehicle.data.qpos, without.vehicle.data.qpos)


def test_linux_camera_uses_picamera2(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Linux backend drives picamera2 like a person would (video configuration, fixed frame duration)."""
    calls: dict[str, Any] = {}

    class Request:
        def make_array(self, name: str) -> np.ndarray:
            return np.full((4, 6, 3), 7, dtype=np.uint8)

        def get_metadata(self) -> dict[str, Any]:
            return {"SensorTimestamp": 5_000_000_000, "FrameCount": 12, "ExposureTime": 8000}

        def release(self) -> None:
            calls["released"] = True

    class Picamera2:
        @staticmethod
        def global_camera_info() -> list[dict[str, Any]]:
            return [{"Id": "/base/axi/pcie@120000/rp1/i2c@80000/imx296@1a"}, {"Id": "/base/axi/pcie@120000/rp1/i2c@88000/imx296@1a"}]

        def __init__(self, index: int) -> None:
            calls["index"] = index

        def create_video_configuration(self, main: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
            calls["main"], calls["controls"] = main, controls
            return {"main": main}

        def configure(self, config: dict[str, Any]) -> None:
            pass

        def start(self) -> None:
            calls["started"] = True

        def capture_request(self, wait: bool = True) -> str:
            return "job"

        def wait(self, job: str, timeout: float) -> Request:
            return Request()

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setitem(sys.modules, "picamera2", types.SimpleNamespace(Picamera2=Picamera2))
    from r3_hal.linux import LinuxCamera

    camera = LinuxCamera("cam0", 6, 4, 30.0, "rgb888")
    frame = camera.capture()
    assert calls["index"] == 1                       # CAM0 is the RP1 i2c@88000 port on a Pi 5
    assert calls["main"] == {"size": (6, 4), "format": "BGR888"}   # picamera2 "BGR888" is stored R, G, B
    assert calls["controls"] == {"FrameDurationLimits": (33333, 33333)}
    assert frame.sequence == 12 and frame.exposure_s == pytest.approx(0.008) and calls["released"]
    assert frame.array().shape == (4, 6, 3)
