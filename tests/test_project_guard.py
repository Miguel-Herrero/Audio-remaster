"""El paso `project` no puede pisar un `<ep>.RPP` editado a mano.

El riesgo es concreto y no hipotetico: la ganancia global es uno de los
parametros del paso, y cambia sola en cuanto se recalcula `loudness` (por
ejemplo al arreglar como se mide el pico). Con eso el paso se considera
desactualizado y, si se ejecuta, reescribe el proyecto encima de las
horas de sync manual que hay dentro.

La comprobacion es exacta porque el manifiesto ya guarda el hash de cada
salida: si el fichero de disco no coincide con el que escribio el paso,
lo ha tocado el usuario.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from remaster.layout import EpisodeLayout
from remaster.manifest import Manifest
from remaster.profile import load_profile
from remaster.reaper.rpp import VolPoint
from remaster.sources import AudioSource, ClassifiedSources, VideoSource
from remaster.steps.align import AlignResult
from remaster.steps.loudness import LoudnessResult
from remaster.steps.project import ProjectEditedError, build_project_step

SR = 48000
NO_SHIFT = AlignResult(offset_seconds=0.0, drift_slope=0.0, confidence=1.0, low_confidence=False)


def _episode(tmp_path: Path):
    layout = EpisodeLayout(root=tmp_path / "s01e01")
    layout.ensure_dirs()
    tone = np.sin(2 * np.pi * 220 * np.arange(SR * 4) / SR).astype(np.float32)
    for path in (layout.en_vocals, layout.en_instrumental, layout.es_vocals, layout.es_instrumental):
        sf.write(path, tone, SR)
    video = layout.sources / "bluray.mkv"
    video.write_bytes(b"")
    layout.align_json_path.write_text("{}")

    sources = ClassifiedSources(
        dvd_container=video,
        dvd_video=VideoSource(video, 0, 720, 576, Fraction(25, 1)),
        es=AudioSource(video, 1, "spa", "ac3"),
        bluray_container=video,
        bluray_video=VideoSource(video, 0, 1920, 1080, Fraction(24000, 1001)),
        en=AudioSource(video, 1, "eng", "pcm_s16le"),
        ja=None, remaster4k=None, target_fps=Fraction(24000, 1001),
    )
    loudness = LoudnessResult(-0.8, (VolPoint(0.0, 1.0), VolPoint(4.0, 1.0)))
    return layout, sources, loudness


def _run(layout, sources, loudness, tmp_path, force=False):
    manifest = Manifest(layout.manifest_path)
    result = build_project_step(
        sources, layout, load_profile(),
        layout.en_vocals, layout.en_instrumental, layout.es_vocals, layout.es_instrumental,
        NO_SHIFT, loudness, manifest, force=force,
    )
    return result, manifest


def test_first_run_writes_the_project(tmp_path):
    layout, sources, loudness = _episode(tmp_path)
    result, _ = _run(layout, sources, loudness, tmp_path)
    assert result.ran
    assert layout.rpp_edit.exists()
    # el fichero de render ya no lo escribe este paso: lo deriva `render`
    assert not layout.rpp_render.exists()


def test_rerunning_untouched_is_a_noop(tmp_path):
    layout, sources, loudness = _episode(tmp_path)
    _run(layout, sources, loudness, tmp_path)
    result, _ = _run(layout, sources, loudness, tmp_path)
    assert not result.ran


def test_hand_edited_project_is_never_overwritten(tmp_path):
    layout, sources, loudness = _episode(tmp_path)
    _run(layout, sources, loudness, tmp_path)

    edited = layout.rpp_edit.read_text() + "\n<!-- horas de sync a mano -->\n"
    layout.rpp_edit.write_text(edited)

    # cambia la ganancia global, como al recalcular loudness: el paso se
    # considera desactualizado y querria reescribir
    louder = LoudnessResult(-3.4, loudness.scene_vol_points)
    with pytest.raises(ProjectEditedError, match="editado a mano"):
        _run(layout, sources, louder, tmp_path)

    assert layout.rpp_edit.read_text() == edited  # intacto


def test_the_error_says_how_to_get_unstuck(tmp_path):
    layout, sources, loudness = _episode(tmp_path)
    _run(layout, sources, loudness, tmp_path)
    layout.rpp_edit.write_text(layout.rpp_edit.read_text() + "\n")

    with pytest.raises(ProjectEditedError) as exc:
        _run(layout, sources, LoudnessResult(-3.4, loudness.scene_vol_points), tmp_path)
    message = str(exc.value)
    assert "render" in message  # saltar el paso
    assert "--force" in message


def test_force_overwrites_but_leaves_a_backup(tmp_path):
    layout, sources, loudness = _episode(tmp_path)
    _run(layout, sources, loudness, tmp_path)
    edited = layout.rpp_edit.read_text() + "\n<!-- trabajo manual -->\n"
    layout.rpp_edit.write_text(edited)

    _run(layout, sources, LoudnessResult(-3.4, loudness.scene_vol_points), tmp_path, force=True)

    assert "trabajo manual" not in layout.rpp_edit.read_text()
    backups = list(layout.project_dir.glob("*.RPP.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == edited


def test_a_project_the_pipeline_never_wrote_is_left_alone(tmp_path):
    """Sin hash previo no se puede saber si esta editado. No se bloquea el
    paso por eso: seria imposible arrancar sobre una carpeta existente.
    """
    layout, sources, loudness = _episode(tmp_path)
    layout.rpp_edit.write_text("<REAPER_PROJECT 0.1 nada 0 0\n>\n")
    result, _ = _run(layout, sources, loudness, tmp_path)
    assert result.ran
