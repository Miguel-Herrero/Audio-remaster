"""Lector de .RPP (remaster/reaper/rpp_read.py).

La prueba fuerte es el viaje de ida y vuelta: se genera un proyecto con
`rpp.py`, se relee con `rpp_read.py` y tienen que salir los mismos
numeros. Asi el lector no puede quedarse atras si cambia el generador.

Aparte se prueban las formas que el generador NUNCA produce pero que si
aparecen en cuanto el usuario edita a mano: items partidos con SOFFS
propio, items muteados y valores entrecomillados.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remaster.reaper.rpp import (
    Item,
    Project,
    RenderSettings,
    Track,
    VolPoint,
    build_project,
    js_volume_fx_lines,
    reaeq_holmes_fx_lines,
)
from remaster.reaper.rpp_read import (
    envelope_gain_db,
    envelope_max_db,
    envelope_mean_db,
    find_track,
    parse_rpp,
    read_tracks,
    tokenize,
)


def _project() -> Project:
    es_vox = Track(
        key="ES_VOX", name="ES VOX",
        items=(
            Item(position=0.0, length=10.0, name="es_vocals.flac", source_file=Path("/tmp/es_vocals.flac")),
            Item(position=12.5, length=7.25, name="es_vocals.flac", source_file=Path("/tmp/es_vocals.flac"), soffs=11.0),
        ),
        vol_points=(VolPoint(0.0, 1.0), VolPoint(10.0, 2.0), VolPoint(20.0, 0.5)),
        fx_lines=js_volume_fx_lines(-1.75, "ep01", "ES_VOX") + reaeq_holmes_fx_lines("ep01", "ES_VOX"),
    )
    en_vox = Track(
        key="EN_VOX", name="EN VOX",
        items=(Item(position=0.0, length=20.0, name="en_vocals.flac", source_file=Path("/tmp/en_vocals.flac")),),
    )
    return Project(
        episode_code="ep01", tracks=(en_vox, es_vox),
        render=RenderSettings(output_file=Path("/tmp/out.flac")),
    )


def test_round_trip_recovers_items_envelope_and_gain():
    tracks = read_tracks(parse_rpp(build_project(_project())))
    es = find_track(tracks, "ES VOX")
    assert es is not None

    assert [i.position for i in es.items] == [0.0, 12.5]
    assert [i.length for i in es.items] == [10.0, 7.25]
    assert [i.soffs for i in es.items] == [0.0, 11.0]
    assert es.items[1].source_file == Path("/tmp/es_vocals.flac")
    assert es.vol_points == ((0.0, 1.0), (10.0, 2.0), (20.0, 0.5))
    assert es.js_volume_db == pytest.approx(-1.75)
    assert es.fx_names[0] == "JS: utility/volume"
    assert any("ReaEQ" in name for name in es.fx_names)


def test_round_trip_finds_every_track():
    tracks = read_tracks(parse_rpp(build_project(_project())))
    assert [t.name for t in tracks] == ["EN VOX", "ES VOX"]
    assert find_track(tracks, "NO EXISTE") is None


def test_track_without_volume_fx_reports_none():
    project = Project(
        episode_code="ep01",
        tracks=(Track(key="EN_VOX", name="EN VOX", items=()),),
        render=RenderSettings(output_file=Path("/tmp/out.flac")),
    )
    track = read_tracks(parse_rpp(build_project(project)))[0]
    assert track.js_volume_db is None
    assert track.vol_points == ()


# --- formas que solo aparecen tras editar a mano ---

_HAND_EDITED = """<REAPER_PROJECT 0.1 "7.71/macOS-arm64" 0 0
  <TRACK {AAAA}
    NAME "ES VOX"
    <VOLENV2
      PT 0 1 0
      PT 5 2 0
    >
    <ITEM
      POSITION 1.5
      LENGTH 2
      MUTE 1 0
      SOFFS 0.25
      PLAYRATE 1 1 0 -1 0 0.0025
      VOLPAN 0.5 0 1 -1
      NAME es_vocals.flac
      <SOURCE FLAC
        FILE "/tmp/es_vocals.flac"
      >
    >
    <ITEM
      POSITION 10
      LENGTH 3
      SOFFS 10
      NAME `raro "con" comillas`
      <SOURCE WAVE
        FILE "/tmp/speech.wav"
      >
    >
  >
>
"""


def test_reads_muted_items_item_volume_and_odd_quotes():
    track = read_tracks(parse_rpp(_HAND_EDITED))[0]
    assert track.name == "ES VOX"
    assert len(track.items) == 2

    muted, patched = track.items
    assert muted.muted is True
    assert muted.volume == pytest.approx(0.5)
    assert muted.soffs == pytest.approx(0.25)

    assert patched.muted is False
    assert patched.source_file == Path("/tmp/speech.wav")
    assert patched.name == 'raro "con" comillas'
    assert track.sources() == ("es_vocals.flac", "speech.wav")


def test_item_at_skips_muted_items():
    track = read_tracks(parse_rpp(_HAND_EDITED))[0]
    assert track.item_at(2.0) is None  # cae dentro del muteado
    assert track.item_at(11.0) is not None


def test_source_time_at_maps_timeline_to_file():
    track = read_tracks(parse_rpp(_HAND_EDITED))[0]
    patched = track.items[1]
    assert patched.source_time_at(10.0) == pytest.approx(10.0)
    assert patched.source_time_at(12.0) == pytest.approx(12.0)


def test_tokenize_handles_the_three_quote_styles():
    assert tokenize('NAME "con espacios"') == ("NAME", "con espacios")
    assert tokenize("NAME 'otra cosa'") == ("NAME", "otra cosa")
    assert tokenize("NAME `con \"comillas\"`") == ("NAME", 'con "comillas"')
    assert tokenize("PT 1.5 2 0") == ("PT", "1.5", "2", "0")


# --- envolvente ---

_POINTS = ((0.0, 1.0), (10.0, 2.0), (20.0, 0.5))


def test_envelope_gain_clamps_outside_the_points():
    assert envelope_gain_db(_POINTS, -5.0) == pytest.approx(0.0)
    assert envelope_gain_db(_POINTS, 100.0) == pytest.approx(20 * -0.3010, abs=0.01)


def test_envelope_gain_interpolates_between_points():
    assert envelope_gain_db(_POINTS, 5.0) == pytest.approx(20 * 0.1761, abs=0.01)  # ganancia 1.5


def test_envelope_mean_and_max_differ_on_a_ramp():
    mean = envelope_mean_db(_POINTS, 0.0, 10.0)
    peak = envelope_max_db(_POINTS, 0.0, 10.0)
    assert peak > mean  # el pico ve el final de la rampa, la media no
    assert peak == pytest.approx(envelope_gain_db(_POINTS, 10.0), abs=0.2)


def test_envelope_without_points_is_unity():
    assert envelope_gain_db((), 3.0) == 0.0
    assert envelope_mean_db((), 0.0, 5.0) == 0.0
