"""Unrelated native renderer experiment; contains no robot or sensor model."""
import mujoco


def test_native_offscreen_scene():
    model = mujoco.MjModel.from_xml_string('<mujoco><worldbody><light pos="0 0 3"/><geom type="sphere" size="0.1" pos="0 0 0.2"/></worldbody></mujoco>')
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    with mujoco.Renderer(model, 64, 64) as renderer:
        renderer.update_scene(data)
        image = renderer.render()
    assert image.shape == (64, 64, 3)
    assert image.max() > image.min()
