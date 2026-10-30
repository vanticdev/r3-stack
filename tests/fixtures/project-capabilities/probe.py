"""Synthetic instrumentation of a native rigid body; no part-specific model."""
from dataclasses import dataclass
import mujoco


@dataclass
class Probe:
    context: object
    steps: int = 0

    def step(self, dt):
        self.steps += 1

    def request(self, operation, payload):
        if operation != "position":
            raise ValueError("unsupported operation")
        if self.steps == 0:
            return None
        body = mujoco.mj_name2id(self.context.model, mujoco.mjtObj.mjOBJ_BODY, self.context.component)
        return {"xyz": self.context.data.xpos[body].tolist(), "steps": self.steps, "t": self.context.t}


def create_device(context):
    return Probe(context)


def create_electrical(component_id, component_type, params, build_context):
    from r3_sim.electrical.models import ConstantLoad
    return ConstantLoad(component_id, component_type, params, build_context, model="project.instrument")
