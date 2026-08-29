"""Tests del signo de sync y la correccion de drift (remaster/steps/project.py).

El signo se verifico contra una medida real hecha a oido en REAPER: con
offset negativo (ES adelantado respecto a EN), la version original
recortaba SOFFS en el sentido que adelantaba ES todavia mas — exactamente
al reves de lo que hacia falta. Los valores de drift_slope de
`test_drift_correction_matches_user_measured_scenario` vienen de dos
timestamps reales medidos por el usuario (0:45.456 ES / 0:47.414 EN al
principio, 36:19.492 ES / 36:23.011 EN al final).
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from remaster.profile import load_profile
from remaster.sources import AudioSource, ClassifiedSources, VideoSource
from remaster.steps import project as project_mod
from remaster.steps.align import AlignResult
from remaster.steps.loudness import LoudnessResult

DURATIONS = {
    "/ep/en_vocals.flac": 100.0,
    "/ep/en_instrumental.flac": 100.0,
    "/ep/es_vocals.flac": 100.0,
    "/ep/es_instrumental.flac": 100.0,
}


def _fake_sources() -> ClassifiedSources:
    bluray = Path("/ep/bluray.mkv")
    dvd = Path("/ep/dvd.mkv")
    return ClassifiedSources(
        dvd_container=dvd,
        dvd_video=VideoSource(dvd, 0, 720, 576, Fraction(25, 1)),
        es=AudioSource(dvd, 1, "spa", "ac3"),
        bluray_container=bluray,
        bluray_video=VideoSource(bluray, 0, 1920, 1080, Fraction(24000, 1001)),
        en=AudioSource(bluray, 1, "eng", "pcm_s16le"),
        ja=None,
        remaster4k=None,
        target_fps=Fraction(24000, 1001),
    )


def _patch_duration(monkeypatch):
    monkeypatch.setattr(project_mod, "_duration", lambda p: DURATIONS[str(p)])


def _paths():
    return (
        Path("/ep/en_vocals.flac"), Path("/ep/en_instrumental.flac"),
        Path("/ep/es_vocals.flac"), Path("/ep/es_instrumental.flac"),
    )


def _find_item(tracks, key):
    return next(t for t in tracks if t.key == key).items[0]


def _build(monkeypatch, align: AlignResult):
    _patch_duration(monkeypatch)
    # No hay audio real en los paths de fixture: si el test dispara
    # segmentacion, se fijan puntos de corte en vez de cargar ficheros.
    monkeypatch.setattr(project_mod, "choose_cut_points", lambda *a, **k: [50.0])
    profile = load_profile()
    en_vox, en_me, es_vox, es_me = _paths()
    loudness = LoudnessResult(global_gain_db=0.0, scene_vol_points=())
    return project_mod.build_tracks(_fake_sources(), profile, en_vox, en_me, es_vox, es_me, align, loudness, "ep")


def test_es_ahead_of_en_gets_delayed_not_trimmed_further(monkeypatch):
    """offset negativo = ES adelantado -> debe RETRASARSE (POSITION>0,
    SOFFS=0), no recortarse mas (que lo adelantaria todavia mas).
    """
    align = AlignResult(offset_seconds=-1.08, drift_slope=0.0, confidence=0.6, low_confidence=False)
    tracks = _build(monkeypatch, align)

    es_item = _find_item(tracks, "ES_VOX")
    assert es_item.position == pytest.approx(1.08)
    assert es_item.soffs == 0.0


def test_es_behind_en_gets_trimmed_not_delayed_further(monkeypatch):
    """offset positivo = ES retrasado -> hay que adelantarlo recortando
    su inicio (SOFFS>0, POSITION=0), no retrasarlo mas.
    """
    align = AlignResult(offset_seconds=1.08, drift_slope=0.0, confidence=0.6, low_confidence=False)
    tracks = _build(monkeypatch, align)

    es_item = _find_item(tracks, "ES_VOX")
    assert es_item.position == 0.0
    assert es_item.soffs == pytest.approx(1.08)


def test_negligible_drift_stays_a_single_item(monkeypatch):
    """Un drift minusculo (por debajo de MIN_TOTAL_DRIFT_TO_SEGMENT_S en
    todo el episodio) no compensa trocear: un solo item, sin PLAYRATE.
    """
    align = AlignResult(offset_seconds=-0.878, drift_slope=-0.00001, confidence=0.6, low_confidence=False)
    tracks = _build(monkeypatch, align)

    es_item = _find_item(tracks, "ES_VOX")
    assert len(next(t for t in tracks if t.key == "ES_VOX").items) == 1
    assert es_item.playrate == 1.0


def test_significant_drift_is_corrected_with_cuts_not_stretching(monkeypatch):
    """Con deriva suficiente en un episodio largo, la correccion se hace
    troceando en varios items con offset creciente — nunca con PLAYRATE.
    Reproduce (a escala) el caso real reportado: 3 tramos de 300s sobre
    700s de episodio, con offset que aumenta segun avanza el tiempo.
    Los puntos de corte se fijan aqui (no se carga audio real): la
    busqueda de silencio esta cubierta aparte en test_scenes.py.
    """
    long_durations = {
        "/ep/en_vocals.flac": 700.0,
        "/ep/en_instrumental.flac": 700.0,
        "/ep/es_vocals.flac": 705.0,
        "/ep/es_instrumental.flac": 705.0,
    }
    monkeypatch.setattr(project_mod, "_duration", lambda p: long_durations[str(p)])
    monkeypatch.setattr(project_mod, "choose_cut_points", lambda *a, **k: [300.0, 600.0])
    profile = load_profile()
    en_vox, en_me, es_vox, es_me = _paths()
    align = AlignResult(offset_seconds=-0.878, drift_slope=-0.00073, confidence=0.6, low_confidence=False)
    loudness = LoudnessResult(global_gain_db=0.0, scene_vol_points=())

    tracks = project_mod.build_tracks(_fake_sources(), profile, en_vox, en_me, es_vox, es_me, align, loudness, "ep")
    es_track = next(t for t in tracks if t.key == "ES_VOX")

    # 700s / 300s por tramo -> 3 items (0-300, 300-600, 600-700).
    assert len(es_track.items) == 3
    # Ninguno estira el audio: siempre PLAYRATE 1.0, solo cortes.
    assert all(item.playrate == 1.0 for item in es_track.items)
    # El offset (visible en position/soffs) crece en valor absoluto con
    # el tiempo: el segundo tramo esta mas corregido que el primero.
    first, second, third = es_track.items
    assert second.position > first.position
    assert third.position > second.position
    # Los tramos son contiguos en la linea de tiempo (sin huecos).
    assert second.position == pytest.approx(first.position + first.length, abs=1e-6)


def test_drift_correction_is_clamped_for_safety(monkeypatch):
    """Una estimacion de drift disparatada (ruido del algoritmo
    automatico) no debe traducirse en cortes agresivos: se limita a
    +-0.005 s/s antes de decidir cuanto trocear.
    """
    long_durations = {
        "/ep/en_vocals.flac": 700.0,
        "/ep/en_instrumental.flac": 700.0,
        "/ep/es_vocals.flac": 900.0,
        "/ep/es_instrumental.flac": 900.0,
    }
    monkeypatch.setattr(project_mod, "_duration", lambda p: long_durations[str(p)])
    monkeypatch.setattr(project_mod, "choose_cut_points", lambda *a, **k: [300.0, 600.0])
    profile = load_profile()
    en_vox, en_me, es_vox, es_me = _paths()
    align = AlignResult(offset_seconds=0.0, drift_slope=-0.5, confidence=0.1, low_confidence=True)
    loudness = LoudnessResult(global_gain_db=0.0, scene_vol_points=())

    tracks = project_mod.build_tracks(_fake_sources(), profile, en_vox, en_me, es_vox, es_me, align, loudness, "ep")
    es_track = next(t for t in tracks if t.key == "ES_VOX")

    # `position` crece con el episodio pase lo que pase (es solo donde
    # cae el tramo en la linea de tiempo); lo que hay que acotar es
    # cuanto se separa `soffs` de `position` — eso es la correccion
    # acumulada. Sin el clamp, drift=-0.5 pediria ~300s de correccion en
    # el ultimo tramo (700s * 0.5); con el clamp a 0.005 s/s, como mucho
    # unos 3.5s.
    max_correction = max(abs(item.soffs - item.position) for item in es_track.items)
    assert max_correction < 5.0


def test_en_tracks_are_never_shifted(monkeypatch):
    """EN VOX/EN M&E son la referencia: su POSITION/SOFFS/PLAYRATE nunca
    cambian, pase lo que pase con el offset o el drift de ES.
    """
    align = AlignResult(offset_seconds=-5.0, drift_slope=0.01, confidence=0.9, low_confidence=False)
    tracks = _build(monkeypatch, align)

    for key in ("EN_VOX", "EN_ME"):
        item = _find_item(tracks, key)
        assert item.position == 0.0
        assert item.soffs == 0.0
        assert item.playrate == 1.0
