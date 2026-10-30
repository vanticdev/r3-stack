"""Seed evidence distinguishes downloaded geometry from qualified CAD and gaps."""
from datetime import datetime

from r3_catalog.modelbank import ModelBank

NEW_SOURCES = {
    'servo-digital-20kg', 'gy-521-mpu6050', 'connector-xt30pw-male',
    'connector-xt30-male', 'connector-xt30-female', 'connector-jst-xh-3-housing',
    'fastener-shcs-m2-16mm', 'fastener-shcs-m2.5-6mm', 'fastener-shcs-m2.5-8mm',
    'fastener-shcs-m3-8mm', 'fastener-shcs-m3-10mm', 'fastener-shcs-m3-12mm',
    'kingpin-screw-m3x20',
}


def test_new_downloads_have_provenance_and_dimension_review():
    bank = ModelBank()
    for identifier in NEW_SOURCES:
        entry = bank.get(identifier)
        source = next(s for s in entry.sources if s.direct and not s.requires_login)
        assert len(source.sha256) == 64
        assert datetime.fromisoformat(source.retrieved_at).tzinfo is not None
        assert source.license and source.format in {'step', 'stp', 'stp.rar'}
        assert entry.validation.bbox_m and entry.validation.expected_bbox_m
        assert entry.validation.tolerance_m == .0015
        assert not entry.proxy_reason
    assert bank.get('servo-digital-20kg').validation.status == 'passed'  # official DS3218 model
    assert bank.get('connector-xt30-female').validation.status == 'mismatch'


def test_every_remaining_proxy_has_specific_followup_evidence():
    for entry in ModelBank().entries():
        if entry.available:
            continue
        assert entry.proxy_reason and 'No exact, publicly downloadable model pinned.' not in entry.proxy_reason
        assert any(s.get('provider') == 'modelbank-follow-up-2026-10-29' and s.get('query')
                   for s in entry.searched_sources), entry.id
