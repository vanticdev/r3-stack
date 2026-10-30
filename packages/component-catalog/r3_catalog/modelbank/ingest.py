"""Small acquisition plugins: installed libraries, official public resource pages and REST search.

Resource-page discovery obeys robots.txt and never follows a login. Database
sources requiring accounts are retained as human-fetch pointers only.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .bank import ModelBank
from .fetch import acquire, checksum, timestamp
from .schema import ModelEntry, Source

PROVIDERS: dict[str, tuple[str, ...]] = {
    'raspberrypi': ('pip.raspberrypi.com', 'pip-assets.raspberrypi.com'),
    'arducam': ('arducam.com', 'docs.arducam.com', 'forum.arducam.com', 'uctronics.com'),
    'pololu': ('pololu.com',),
    'ublox': ('u-blox.com', 'content.u-blox.com'),
    'sparkfun': ('sparkfun.com', 'cdn.sparkfun.com', 'docs.sparkfun.com', 'github.com', 'raw.githubusercontent.com'),
    'adafruit': ('adafruit.com', 'cdn-shop.adafruit.com', 'learn.adafruit.com', 'github.com', 'raw.githubusercontent.com'),
    'traceparts': ('traceparts.com',),
    '3dcontentcentral': ('3dcontentcentral.com',),
}


def slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href' and v)


def allowed_host(url: str, hosts: tuple[str, ...]) -> bool:
    host = urllib.parse.urlparse(url).hostname or ''
    return any(host == h or host.endswith('.' + h) for h in hosts)


def public_page(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    robots = urllib.robotparser.RobotFileParser(f'{parsed.scheme}://{parsed.netloc}/robots.txt')
    try:
        robots.read()
    except OSError as error:
        raise ValueError(f'cannot establish robots policy: {error}') from error
    if not robots.can_fetch('R3-modelbank', url):
        raise ValueError(f'robots.txt forbids {url}')
    request = urllib.request.Request(url, headers={'User-Agent': 'R3-modelbank/1.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(4 * 1024 * 1024).decode('utf-8', errors='replace')


def ingest_page(provider: str, url: str, *, name: str, category: str = 'other', mpn: str = '',
                manufacturer: str = '', html: str | None = None, pin: bool = True) -> list[ModelEntry]:
    hosts = PROVIDERS[provider]
    if not allowed_host(url, hosts):
        raise ValueError(f'{provider} ingestor only accepts its official public resource hosts')
    if provider in ('traceparts', '3dcontentcentral'):
        return [ModelEntry(id=slug(f'{provider}-{mpn or name}'), name=name, manufacturer=manufacturer, mpn=mpn,
                           category=category, sources=[Source(kind='database', url=url, requires_login=True, direct=False)],
                           proxy_reason='Account-gated model: a human must fetch under the source terms.')]
    parser = Links()
    resource_path = urllib.parse.unquote(urllib.parse.urlparse(url).path).lower()
    is_direct = bool(re.search(r'\.(step|stp)$', resource_path) or (resource_path.endswith('.zip') and 'step' in resource_path))
    if is_direct:
        parser.links = [url]
    else:
        parser.feed(html if html is not None else public_page(url))
    entries = []
    for link in sorted(set(parser.links)):
        direct = urllib.parse.urljoin(url, link)
        path = urllib.parse.unquote(urllib.parse.urlparse(direct).path)
        if not (re.search(r'\.(step|stp)(\.zip)?$', path, re.I) or (path.lower().endswith('.zip') and 'step' in path.lower())) or not allowed_host(direct, hosts):
            continue
        fmt = 'stp.zip' if path.lower().endswith('.zip') else path.rsplit('.', 1)[-1].lower()
        source = Source(kind='vendor_step', url=direct, format=fmt)
        if pin:
            _, source = acquire(source, pin=True)
        entry = ModelEntry(id=slug(f'{provider}-{mpn or Path(path).stem}'), name=name, manufacturer=manufacturer or provider,
                           mpn=mpn, category=category, sources=[source], tags=[provider],
                           searched_sources=[{'provider': provider, 'url': url, 'result': 'public CAD link', 'date': timestamp()}])
        # Multiple CAD variants on one page must keep distinct IDs.
        if any(e.id == entry.id for e in entries):
            entry.id += '-' + slug(Path(path).stem)
        entries.append(entry)
    return entries


def ingest_kicad(root: Path | None = None, *, query: str = '') -> list[ModelEntry]:
    if root is None:
        from r3_connectors.kicad import model_dir
        root = model_dir()
        if root is None:
            raise FileNotFoundError('KiCad 3D model library is not installed')
    entries = []
    for path in sorted(root.rglob('*.step')):
        relative = path.relative_to(root).as_posix()
        if query.lower() not in relative.lower():
            continue
        footprint = relative.replace('.3dshapes/', ':').removesuffix('.step')
        # Store the official portable library URL, never a workstation identity.
        url = 'https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/' + urllib.parse.quote(relative)
        source = Source(kind='kicad_3d', url=url, sha256=checksum(path), retrieved_at=timestamp(),
                        license='CC-BY-SA-4.0 with KiCad libraries exception')
        # Copy installed bytes to the content-addressed cache while preserving portable provenance.
        acquire(source, downloader=lambda _url, target, p=path: _copy(p, target))
        entries.append(ModelEntry(id='kicad-' + slug(relative), name=path.stem, manufacturer='KiCad library',
                                  category='connector' if 'Connector' in relative else 'other',
                                  tags=['kicad', footprint], aliases=[footprint], sources=[source]))
    return entries


def _copy(path: Path, target: Path) -> Path:
    import shutil
    shutil.copyfile(path, target)
    return target


def ingest_onshape(query: str, transport: Any) -> list[ModelEntry]:
    """Search public documents only; only immutable versions become insertable models."""
    response = transport.json('GET', '/documents', query={'q': query, 'filter': 2, 'limit': 50})
    entries = []
    for document in response.get('items', []):
        if document.get('isPublic') is not True:
            continue
        did = document['id']
        versions = transport.json('GET', f'/documents/d/{did}/versions')
        if not versions:
            continue
        vid = versions[-1]['id']
        elements = transport.json('GET', f'/documents/d/{did}/v/{vid}/elements')
        for element in elements:
            if element.get('elementType') != 'PARTSTUDIO':
                continue
            eid = element['id']
            entries.append(ModelEntry(id=f'onshape-{did}-{eid}', name=f"{document['name']} / {element['name']}", category='other',
                                      sources=[Source(kind='onshape_public', url=f'https://cad.onshape.com/documents/{did}/v/{vid}/e/{eid}',
                                                      did=did, vid=vid, eid=eid)],
                                      searched_sources=[{'provider': 'onshape_public', 'query': query, 'date': timestamp()}]))
    return entries


def ingest(bank: ModelBank, provider: str, **options: Any) -> dict[str, Any]:
    if provider == 'kicad':
        root = options.get('root')
        entries = ingest_kicad(Path(root) if root else None, query=options.get('query', ''))
    elif provider == 'onshape':
        from r3_onshape.client import default_transport
        transport = default_transport()
        if transport is None:
            raise ValueError('Onshape public search requires a connected transport (API keys or browser bridge)')
        entries = ingest_onshape(options.get('query', ''), transport)
    else:
        entries = ingest_page(provider, options['url'], name=options.get('name') or provider,
                              category=options.get('category', 'other'), mpn=options.get('mpn', ''))
    added, existing = [], []
    ids = {e.id for e in bank.entries()}
    for entry in entries:
        if entry.id in ids:
            existing.append(entry.id)
        else:
            bank.put(entry)
            ids.add(entry.id)
            added.append(entry.id)
    return {'ok': True, 'added': added, 'existing': existing, 'provider': provider}
