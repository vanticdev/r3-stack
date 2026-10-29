"""Model-bank CLI registration kept separate from pipeline command implementations."""
from __future__ import annotations

import argparse
import json
from typing import Any


def register(sub: Any, common: argparse.ArgumentParser) -> None:
    from r3_catalog.modelbank import service
    from r3_catalog.modelbank.bank import ModelBank
    from r3_catalog.modelbank.ingest import PROVIDERS, ingest

    parent = sub.add_parser('models', parents=[common], help='Search, acquire and validate purchased-part CAD.')
    commands = parent.add_subparsers(dest='model_action', required=True)
    p = commands.add_parser('search', parents=[common])
    p.add_argument('query', nargs='?', default='')
    p.add_argument('--category')
    p.add_argument('--manufacturer')
    p.add_argument('--specs', default='{}', help='JSON spec ranges in SI: {"mass": [0, 0.1]}')
    p.add_argument('--limit', type=int, default=30)
    p.set_defaults(func=lambda a: service.guard(lambda: service.search(a.query, a.category, a.manufacturer, json.loads(a.specs), a.limit)))
    for action, function in [('show', service.show), ('fetch', service.fetch_model), ('validate', service.validate_model)]:
        p = commands.add_parser(action, parents=[common])
        p.add_argument('model_id')
        p.set_defaults(func=lambda a, f=function: service.guard(lambda: f(a.model_id)))
    p = commands.add_parser('add', parents=[common])
    p.add_argument('--name', required=True)
    p.add_argument('--mpn', default='')
    p.add_argument('--manufacturer', default='')
    p.add_argument('--category', required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--url', default='')
    source.add_argument('--onshape', default='')
    p.add_argument('--id', dest='model_id', default='')
    p.add_argument('--license', default='unspecified; verify redistribution terms')
    p.add_argument('--format', default='step', choices=['step', 'stp', 'stp.zip', 'stp.rar', 'glb', 'stl', 'wrl'])
    p.add_argument('--member', default='')
    p.add_argument('--catalog-component')
    fields = ['name', 'category', 'mpn', 'manufacturer', 'url', 'onshape', 'model_id', 'license', 'format', 'member', 'catalog_component']
    p.set_defaults(func=lambda a: service.guard(lambda: service.add(**{k: getattr(a, k) for k in fields})))
    p = commands.add_parser('ingest', parents=[common])
    p.add_argument('provider', choices=['kicad', 'onshape', *PROVIDERS])
    for field in ['url', 'name', 'mpn', 'root', 'query']:
        p.add_argument('--' + field, default='')
    p.add_argument('--category', default='other')
    p.set_defaults(func=lambda a: service.guard(lambda: ingest(ModelBank(), a.provider, **{k: getattr(a, k) for k in ['url', 'name', 'mpn', 'root', 'query', 'category']})))
