"""Typed design demands for an explicitly identified purchased assembly."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .units import Quantity

RatingMetric = Literal["torque", "angular_speed", "force", "linear_speed"]


class RatedJointDemand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: str
    joint: str
    demand: dict[RatingMetric, Quantity] = Field(default_factory=dict)
    source: str = ""
