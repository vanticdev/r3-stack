"""Model bank HTTP routes; cached assets are addressed by model ID and fixed format."""
from pathlib import Path
from typing import Literal
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from r3_catalog.modelbank import service
from .errors import ApiError

router = APIRouter(prefix='/api/models', tags=['model bank'])


class AddModel(BaseModel):
    name: str
    category: str
    mpn: str = ''
    manufacturer: str = ''
    url: str = ''
    onshape: str = ''
    model_id: str = ''
    license: str = 'unspecified; verify redistribution terms'
    format: str = 'step'
    member: str = ''
    catalog_component: str | None = None


def checked(result: dict) -> dict:
    if not result.get('ok'):
        raise ApiError(result.get('code', 'model_bank_error'), result.get('message', 'Model operation failed'),
                       {k: v for k, v in result.items() if k not in ('ok', 'code', 'message')}, status=422)
    return result


@router.get('')
def search(q: str = '', category: str | None = None, manufacturer: str | None = None, specs: str = '{}', limit: int = 30) -> dict:
    return checked(service.guard(lambda: service.search(q, category, manufacturer, json.loads(specs), limit)))


@router.post('')
def add(body: AddModel) -> dict:
    # HTTP users cannot read arbitrary worker-local files through model acquisition.
    if body.url and not body.url.startswith(('https://', 'http://')):
        raise HTTPException(422, 'HTTP model source must use https or http')
    return checked(service.guard(lambda: service.add(**body.model_dump())))


@router.get('/{model_id}')
def show(model_id: str) -> dict:
    return checked(service.guard(lambda: service.show(model_id)))


@router.post('/{model_id}/fetch')
def fetch(model_id: str) -> dict:
    result = checked(service.guard(lambda: service.fetch_model(model_id)))
    return {**result, 'glb_url': f'/api/models/{model_id}/assets/glb', 'stl_url': f'/api/models/{model_id}/assets/stl'}


@router.post('/{model_id}/validate')
def validate(model_id: str) -> dict:
    return checked(service.guard(lambda: service.validate_model(model_id)))


@router.get('/{model_id}/assets/{format}')
def asset(model_id: str, format: Literal['glb', 'stl']) -> FileResponse:
    from r3_catalog.modelbank import ModelBank
    from r3_catalog.modelbank.bank import cache_root
    result = checked(service.guard(lambda: service.show(model_id)))
    entry = ModelBank().get(result['model']['id'])
    for source in entry.sources:
        if source.sha256:
            path = cache_root() / source.sha256 / ('model.' + format)
            if path.is_file():
                return FileResponse(path, media_type='model/gltf-binary' if format == 'glb' else 'model/stl')
    raise HTTPException(404, 'Fetch the model before requesting its assets')


class IngestModels(BaseModel):
    provider: Literal['kicad', 'onshape', 'raspberrypi', 'arducam', 'pololu', 'ublox', 'sparkfun', 'adafruit', 'traceparts', '3dcontentcentral']
    url: str = ''
    name: str = ''
    category: str = 'other'
    mpn: str = ''
    query: str = ''


@router.post('/ingest/run')
def ingest(body: IngestModels) -> dict:
    from r3_catalog.modelbank import ModelBank
    from r3_catalog.modelbank.ingest import ingest as run
    return checked(service.guard(lambda: run(ModelBank(), **body.model_dump())))
