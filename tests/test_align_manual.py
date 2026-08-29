"""Tests de la calibracion manual de sync (remaster/steps/align.py).

Existe para el caso — real, visto en produccion — donde GCC-PHAT no
converge de forma fiable (separacion con bleed, dub con timing propio por
escena) pero el usuario puede medir dos puntos limpios a oido en REAPER.
"""

from __future__ import annotations

import json

from remaster.layout import EpisodeLayout
from remaster.manifest import Manifest
from remaster.steps.align import manual_align


def test_manual_align_writes_exact_values(tmp_path):
    layout = EpisodeLayout(root=tmp_path)
    layout.ensure_dirs()
    manifest = Manifest(layout.manifest_path)

    result, step = manual_align(-0.844, -0.0007309, layout, manifest)

    assert step.ran is True
    assert result.offset_seconds == -0.844
    assert result.drift_slope == -0.0007309
    assert result.confidence == 1.0
    assert result.low_confidence is False

    data = json.loads(layout.align_json_path.read_text())
    assert data["manual_override"] is True


def test_manual_align_rerun_with_same_values_is_noop(tmp_path):
    layout = EpisodeLayout(root=tmp_path)
    layout.ensure_dirs()
    manifest = Manifest(layout.manifest_path)

    manual_align(-0.844, -0.0007309, layout, manifest)
    manifest2 = Manifest(layout.manifest_path)
    _result, step2 = manual_align(-0.844, -0.0007309, layout, manifest2)

    assert step2.ran is False


def test_manual_align_with_different_values_reruns(tmp_path):
    layout = EpisodeLayout(root=tmp_path)
    layout.ensure_dirs()
    manifest = Manifest(layout.manifest_path)

    manual_align(-0.844, -0.0007309, layout, manifest)
    manifest2 = Manifest(layout.manifest_path)
    result2, step2 = manual_align(-1.0, 0.0, layout, manifest2)

    assert step2.ran is True
    assert result2.offset_seconds == -1.0


def test_manual_align_removes_stale_diagnostic_png(tmp_path):
    layout = EpisodeLayout(root=tmp_path)
    layout.ensure_dirs()
    layout.align_png_path.write_bytes(b"fake png from a previous automatic run")
    manifest = Manifest(layout.manifest_path)

    manual_align(-0.844, -0.0007309, layout, manifest)

    assert not layout.align_png_path.exists()
