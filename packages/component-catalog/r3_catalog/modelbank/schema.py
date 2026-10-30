"""Human-editable model records. Measurements and provenance are never inferred from a name."""
from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

Category = Literal['computer', 'camera', 'motor', 'gearbox', 'esc', 'servo', 'battery', 'gnss', 'imu',
                   'receiver', 'connector', 'wheel', 'fastener', 'regulator', 'sensor', 'cable', 'bearing', 'other']


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Spec(Record):
    value: float | list[float]
    unit: Literal['m', 'kg', 'V', 'A', 'rad', 'm/s', 'N*m', '1']
    source: str = ''
    status: Literal['datasheet', 'vendor', 'measured', 'estimated'] = 'datasheet'


class Source(Record):
    kind: Literal['vendor_step', 'onshape_public', 'kicad_3d', 'database', 'local']
    url: str
    format: Literal['step', 'stp', 'stp.zip', 'stp.rar', 'wrl', 'glb', 'stl'] = 'step'
    license: str = 'unspecified; verify redistribution terms'
    sha256: str = Field('', pattern=r'^([a-f0-9]{64})?$')
    retrieved_at: str = ''
    requires_login: bool = False
    # A landing page is a useful pointer, but must never be downloaded as CAD.
    direct: bool = True
    member: str = ''
    solid_indices: list[int] = Field(default_factory=list, description='Zero-based solids retained from the supplied STEP; '
                                    'select complete purchased subparts without modifying their geometry.')
    did: str = ''
    vid: str = ''
    eid: str = ''
    part_id: str = Field('', validation_alias=AliasChoices('part_id', 'partId'))

    @model_validator(mode='after')
    def valid_location(self) -> Source:
        if any(i < 0 for i in self.solid_indices) or len(set(self.solid_indices)) != len(self.solid_indices):
            raise ValueError('solid_indices must be distinct nonnegative indices')
        if urlparse(self.url).scheme not in ('https', 'http', 'file'):
            raise ValueError('model URL must use https, http or file')
        if self.kind == 'onshape_public' and not all((self.did, self.vid, self.eid)):
            raise ValueError('public Onshape models require immutable did/vid/eid')
        return self


class Validation(Record):
    status: Literal['unverified', 'passed', 'mismatch'] = 'unverified'
    bbox_m: list[float] | None = Field(None, min_length=3, max_length=3)
    expected_bbox_m: list[float] | None = Field(None, min_length=3, max_length=3)
    tolerance_m: float = Field(0.0015, ge=0)
    key_features: dict[str, bool] = Field(default_factory=dict)
    date: str = ''
    validated_by: str = ''
    notes: str = ''


class ModelEntry(Record):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]*$')
    name: str = Field(min_length=1)
    manufacturer: str = ''
    mpn: str = ''
    aliases: list[str] = Field(default_factory=list)
    category: Category
    tags: list[str] = Field(default_factory=list)
    key_specs: dict[str, Spec] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
    catalog_component: str | None = None
    catalog_aliases: list[str] = Field(default_factory=list)
    searched_sources: list[dict[str, Any]] = Field(default_factory=list)
    proxy_reason: str = ''
    # Real geometry frame in component coordinates, in metres. Reviewed before CAD insertion.
    placement: dict[str, list[float]] | None = None
    interface_frames: dict[str, dict[str, list[float]]] = Field(default_factory=dict)
    onshape_connectors: dict[str, str] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        return any(s.direct and not s.requires_login for s in self.sources)
