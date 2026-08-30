"""Re-analisis del proyecto ya editado a mano (remaster/steps/verify.py).

El caso que motiva el paso, visto en s03e01: la pista ES VOX acaba con
105 s de audio que NO es castellano — trozos de `en_vocals.flac` con los
que el usuario rellena huecos porque suenan mejor. Eso es legitimo. Lo
que no lo es: la envolvente calculada para subir el castellano flojo
sigue aplicandose ahi, asi que el ingles suena hasta 6.8 dB por encima
del ingles original. Medido en el render real: +4.9 dB en el tramo
42:17–42:59.

Dos trampas que este test fija, las dos aprendidas en datos reales:

  * la deteccion no puede depender de que EN VOX tenga un item solapando,
    porque los parches caen justo en los huecos de EN VOX;
  * comparar el LUFS de un item corto de doblaje contra el ingles del
    mismo instante mide sobre todo que el fraseo no coincide, no el
    volumen. Donde el ingles calla no hay nada que juzgar.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from remaster.layout import EpisodeLayout
from remaster.profile import load_profile
from remaster.reaper.rpp import (
    Item,
    Project,
    RenderSettings,
    Track,
    VolPoint,
    build_project,
    js_volume_fx_lines,
)
from remaster.steps.verify import (
    STATUS_DOUBLE_GAIN,
    STATUS_NO_REFERENCE,
    STATUS_OFF,
    STATUS_OK,
    VerifyError,
    verify_project,
)

SR = 48000
DURATION = 40.0
GLOBAL_GAIN_DB = -0.8


def _speech(seconds: float, amplitude: float, seed: int = 0) -> np.ndarray:
    """Ruido rosa-ish a un nivel dado: sirve de sustituto de voz para
    medir LUFS, que es lo unico que se compara aqui.
    """
    rng = np.random.default_rng(seed)
    return (rng.normal(0.0, amplitude, int(SR * seconds))).astype(np.float32)


def _build_episode(tmp_path: Path, envelope_over_patch_db: float) -> EpisodeLayout:
    """Un episodio de 40 s con:
      * castellano a nivel correcto en 0–20 s,
      * un parche de INGLES en su sitio en 20–30 s,
      * castellano otra vez en 30–40 s.
    La envolvente sube `envelope_over_patch_db` justo sobre el parche.
    """
    layout = EpisodeLayout(root=tmp_path / "s01e01")
    layout.ensure_dirs()

    en = np.concatenate([_speech(DURATION, 0.05, seed=1)])
    es = np.concatenate([_speech(DURATION, 0.05, seed=2)])
    sf.write(layout.en_vocals, en, SR)
    sf.write(layout.es_vocals, es, SR)

    gain = 10 ** (envelope_over_patch_db / 20)
    vol_points = (
        VolPoint(0.0, 1.0), VolPoint(19.9, 1.0),
        VolPoint(20.0, gain), VolPoint(29.9, gain),
        VolPoint(30.0, 1.0), VolPoint(DURATION, 1.0),
    )

    es_vox = Track(
        key="ES_VOX", name="ES VOX",
        vol_points=vol_points,
        fx_lines=js_volume_fx_lines(GLOBAL_GAIN_DB, "s01e01", "ES_VOX"),
        items=(
            Item(position=0.0, length=20.0, soffs=0.0, name="es", source_file=layout.es_vocals),
            # el parche: fuente inglesa, en su propio instante (soffs == position)
            Item(position=20.0, length=10.0, soffs=20.0, name="en", source_file=layout.en_vocals),
            Item(position=30.0, length=10.0, soffs=30.0, name="es", source_file=layout.es_vocals),
        ),
    )
    # EN VOX con un HUECO de 20 a 30: el parche cae justo ahi, como en el
    # proyecto real. Si la deteccion buscase "el item de EN VOX que
    # solapa", aqui no encontraria ninguno.
    en_vox = Track(
        key="EN_VOX", name="EN VOX",
        items=(
            Item(position=0.0, length=20.0, soffs=0.0, name="en", source_file=layout.en_vocals),
            Item(position=30.0, length=10.0, soffs=30.0, name="en", source_file=layout.en_vocals),
        ),
    )
    project = Project(
        episode_code="s01e01", tracks=(en_vox, es_vox),
        render=RenderSettings(output_file=layout.es_reconstructed),
    )
    layout.rpp_edit.write_text(build_project(project))
    return layout


def test_english_patch_with_envelope_on_top_is_flagged(tmp_path):
    layout = _build_episode(tmp_path, envelope_over_patch_db=6.8)
    report = verify_project(layout, load_profile(), stats_path=None)

    patch = next(i for i in report.items if i.start == pytest.approx(20.0))
    assert patch.status == STATUS_DOUBLE_GAIN
    assert patch.source == "en_vocals.flac"
    # ganancia global + envolvente: eso es exactamente el desequilibrio
    assert patch.delta_db == pytest.approx(GLOBAL_GAIN_DB + 6.8, abs=0.1)
    assert patch in report.flagged_items


def test_english_patch_without_envelope_is_not_flagged(tmp_path):
    """El mismo parche, mismo fichero, misma posicion — pero sin ganancia
    encima. No hay nada que arreglar y no debe salir marcado.
    """
    layout = _build_episode(tmp_path, envelope_over_patch_db=0.0)
    report = verify_project(layout, load_profile(), stats_path=None)

    patch = next(i for i in report.items if i.start == pytest.approx(20.0))
    assert patch.status == STATUS_OK
    assert not report.flagged_items


def test_detection_survives_the_gap_in_the_english_track(tmp_path):
    """Fija el motivo por el que la deteccion mira el mapeo
    (`position - soffs`) y no los items de EN VOX: en el instante del
    parche, EN VOX no tiene item ninguno.
    """
    from remaster.reaper.rpp_read import find_track, load_rpp, read_tracks

    layout = _build_episode(tmp_path, envelope_over_patch_db=6.8)
    tracks = read_tracks(load_rpp(layout.rpp_edit))
    en_track = find_track(tracks, "EN VOX")
    assert en_track.item_at(25.0) is None  # el hueco existe de verdad

    report = verify_project(layout, load_profile(), stats_path=None)
    assert any(i.status == STATUS_DOUBLE_GAIN for i in report.items)


def test_reads_the_project_not_the_cached_loudness(tmp_path):
    layout = _build_episode(tmp_path, envelope_over_patch_db=3.0)
    report = verify_project(layout, load_profile(), stats_path=None)

    assert report.global_gain_db == pytest.approx(GLOBAL_GAIN_DB, abs=0.01)
    assert report.item_count == 3
    assert set(report.sources) == {"es_vocals.flac", "en_vocals.flac"}
    assert report.envelope_points == 6


def test_silent_english_gives_no_reference_instead_of_a_fake_deviation(tmp_path):
    """Donde el ingles calla, un item de doblaje no se puede juzgar. Antes
    salia como "desviado +14 dB" y llenaba el informe de ruido.
    """
    layout = _build_episode(tmp_path, envelope_over_patch_db=0.0)
    # silencio en el ingles durante todo el primer tramo
    en = np.zeros(int(SR * DURATION), dtype=np.float32)
    en[int(SR * 30):] = _speech(10.0, 0.05, seed=3)
    sf.write(layout.en_vocals, en, SR)

    report = verify_project(layout, load_profile(), stats_path=None)
    first = next(i for i in report.items if i.start == pytest.approx(0.0))
    assert first.status == STATUS_NO_REFERENCE
    assert first not in report.flagged_items


def test_loud_dub_over_quiet_english_is_flagged(tmp_path):
    """El otro lado: cuando SI hay ingles con el que comparar y el
    castellano se sale, tiene que salir marcado.
    """
    layout = _build_episode(tmp_path, envelope_over_patch_db=0.0)
    es = _speech(DURATION, 0.05, seed=2)
    es[: int(SR * 20)] *= 10 ** (9.0 / 20)  # +9 dB en el primer tramo
    sf.write(layout.es_vocals, es, SR)

    report = verify_project(layout, load_profile(), stats_path=None)
    first = next(i for i in report.items if i.start == pytest.approx(0.0))
    assert first.status == STATUS_OFF
    assert first.delta_db > 6.0


def test_missing_project_is_a_clear_error(tmp_path):
    layout = EpisodeLayout(root=tmp_path / "s01e02")
    layout.ensure_dirs()
    with pytest.raises(VerifyError, match="No hay proyecto"):
        verify_project(layout, load_profile(), stats_path=None)


def test_warns_when_there_is_no_render_stats(tmp_path):
    layout = _build_episode(tmp_path, envelope_over_patch_db=0.0)
    report = verify_project(layout, load_profile(), stats_path=None)
    assert report.global_check is None
    assert any("dry run" in w for w in report.warnings)
