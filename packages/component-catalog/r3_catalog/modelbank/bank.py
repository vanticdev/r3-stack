"""YAML source of truth with a disposable, content-invalidated SQLite FTS5 index."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from .schema import ModelEntry

DATA = Path(__file__).parent / 'data'


def cache_root() -> Path:
    return Path(os.environ.get('R3_MODEL_CACHE', Path.home() / '.cache/r3/models'))


def normalized(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.lower())


class ModelBank:
    _datasets: dict[str, tuple[tuple, list[ModelEntry]]] = {}

    def __init__(self, dataset: str | Path | None = None, index: str | Path | None = None) -> None:
        self.dataset = Path(dataset or os.environ.get('R3_MODEL_BANK', DATA))
        key = hashlib.sha256(str(self.dataset.resolve()).encode()).hexdigest()[:16]
        self.index = Path(index) if index else cache_root() / f'index-{key}.sqlite'

    def entries(self, *, copy: bool = True) -> list[ModelEntry]:
        paths = sorted(self.dataset.glob('*.yaml'))
        signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        key = str(self.dataset.resolve())
        cached = self._datasets.get(key)
        if cached is not None and cached[0] == signature:
            return [e.model_copy(deep=True) for e in cached[1]] if copy else cached[1]
        records = [ModelEntry.model_validate(yaml.safe_load(p.read_text())) for p in paths]
        ids = [r.id for r in records]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate model-bank id')
        self._datasets[key] = (signature, records)
        return [e.model_copy(deep=True) for e in records] if copy else records

    def get(self, model_id: str) -> ModelEntry:
        # Look up validated IDs, never interpolate user input into a filesystem path.
        entry = next((r for r in self.entries(copy=False) if r.id == model_id), None)
        return entry.model_copy(deep=True) if entry else self._missing(model_id)

    @staticmethod
    def _missing(model_id: str) -> Any:
        raise KeyError(f'unknown model: {model_id}')

    def put(self, entry: ModelEntry, *, replace: bool = False) -> ModelEntry:
        self.dataset.mkdir(parents=True, exist_ok=True)
        target = self.dataset / f'{entry.id}.yaml'
        content = yaml.safe_dump(entry.model_dump(mode='json'), sort_keys=False, allow_unicode=True)
        if not replace:
            with target.open('x') as out:
                out.write(content)
        else:
            fd, temporary = tempfile.mkstemp(dir=self.dataset, suffix='.tmp')
            try:
                with os.fdopen(fd, 'w') as out:
                    out.write(content)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return entry

    def _connect(self) -> sqlite3.Connection:
        entries = self.entries(copy=False)
        digest = hashlib.sha256(json.dumps([e.model_dump(mode='json') for e in entries], sort_keys=True).encode()).hexdigest()
        self.index.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.index, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS revision (digest TEXT)')
            current = db.execute('SELECT digest FROM revision').fetchone()
            if current is None or current[0] != digest:
                db.execute('DROP TABLE IF EXISTS models')
                db.execute('DROP TABLE IF EXISTS search')
                db.execute('CREATE TABLE models (id TEXT PRIMARY KEY, category TEXT, manufacturer TEXT, record TEXT)')
                db.execute('CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED, text, tokenize="unicode61 remove_diacritics 2")')
                for e in entries:
                    db.execute('INSERT INTO models VALUES (?,?,?,?)', (e.id, e.category, e.manufacturer, e.model_dump_json()))
                    db.execute('INSERT INTO search VALUES (?,?)', (e.id, ' '.join([e.name, e.manufacturer, e.mpn, *e.aliases, *e.tags, e.category])))
                db.execute('DELETE FROM revision')
                db.execute('INSERT INTO revision VALUES (?)', (digest,))
            db.commit()
            return db
        except Exception:
            db.close()
            raise

    def search(self, query: str = '', *, category: str | None = None, manufacturer: str | None = None,
               specs: dict[str, list[float | None]] | None = None, limit: int = 30) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        with closing(self._connect()) as db:
            conditions, values = [], []
            if category:
                conditions.append('category = ?'); values.append(category)
            if manufacturer:
                conditions.append('lower(manufacturer) = lower(?)'); values.append(manufacturer)
            for name, bounds in (specs or {}).items():
                if not re.fullmatch(r'[a-zA-Z0-9_]+', name) or len(bounds) != 2:
                    raise ValueError('spec ranges are {spec_name: [minimum, maximum]} in SI')
                for op, value in zip(('>=', '<='), bounds):
                    if value is not None:
                        conditions.append(f"CAST(json_extract(record, '$.key_specs.{name}.value') AS REAL) {op} ?")
                        values.append(float(value))
            rows = db.execute('SELECT * FROM models' + (' WHERE ' + ' AND '.join(conditions) if conditions else ''), values).fetchall()
            tokens = re.findall(r'\w+', query)
            fts = {}
            if tokens:
                expression = ' OR '.join('"' + t.replace('"', '""') + '"*' for t in tokens)
                fts = {r['id']: r['rank'] for r in db.execute('SELECT id, bm25(search) AS rank FROM search WHERE search MATCH ?', (expression,))}
            results = []
            q = normalized(query)
            for row in rows:
                record = json.loads(row['record'])
                variants = [record['mpn'], record['id'], *record['aliases']]
                fuzzy = max((SequenceMatcher(None, q, normalized(v)).ratio() for v in variants if v), default=0)
                exact = bool(q and any(q == normalized(v) for v in variants))
                if query and row['id'] not in fts and fuzzy < 0.65:
                    continue
                score = (100 if exact else 0) + (30 if row['id'] in fts else 0) + 20 * fuzzy - fts.get(row['id'], 0)
                results.append({**record, 'score': round(score, 6), 'available': any(s['direct'] and not s['requires_login'] for s in record['sources'])})
            return sorted(results, key=lambda r: (-r['score'], r['id']))[:limit]

    def resolve(self, component: Any) -> ModelEntry | None:
        ref = getattr(component, 'model', None)
        bank_id = (getattr(ref, 'params', {}) or {}).get('bank_id')
        if bank_id:
            return self.get(bank_id)
        matches = [e for e in self.entries(copy=False) if component.id in [e.catalog_component, *e.catalog_aliases]]
        if len(matches) > 1:
            raise ValueError(f'ambiguous bank models for {component.id}; set model.params.bank_id')
        return matches[0].model_copy(deep=True) if matches else None
