"""Derivar `<ep>_render.RPP` del `<ep>.RPP` editado (rpp_render.py).

Bug real, medido en s03e01: el proyecto editado tenia 352 items tras dia
y medio de sync manual y el de render seguia teniendo 25, los recien
generados. Renderizar por el pipeline habria tirado todo ese trabajo.

Y la solucion obvia — renderizar directamente el editado — tampoco vale:
en ese mismo proyecto `RENDER_FILE` apuntaba a `hudson_risa.flac`, un
render suelto que el usuario habia hecho para escuchar una risa. Habria
escrito ahi en vez de en `es_reconstructed.flac`. De ahi que el fichero
de render siga existiendo, pero como copia del editado con el destino y
los mutes forzados.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remaster.reaper.rpp import RENDER_CFG_FLAC16
from remaster.reaper.rpp_read import find_track, parse_rpp, read_tracks
from remaster.reaper.rpp_render import (
    RenderCopyPlan,
    RenderProjectError,
    derive_render_project,
    write_render_project,
)

_EDITED = """<REAPER_PROJECT 0.1 "7.71/macOS-arm64" 0 0
  SAMPLERATE 48000 1 0
  RENDER_FILE "/Users/x/Movies/s03e01/02_finals/hudson_risa.flac"
  RENDER_FMT 0 1 0
  RENDER_RANGE 2 0 0 0 1000
  RENDER_DITHER 1
  <RENDER_CFG
    QWxnbwBWaWVqbw==
  >
  <TRACK {AAAA}
    NAME "EN VOX"
    MUTESOLO 0 0 0
    <ITEM
      POSITION 0
      LENGTH 10
      NAME en_vocals.flac
      <SOURCE FLAC
        FILE "/tmp/en_vocals.flac"
      >
    >
  >
  <TRACK {BBBB}
    NAME "EN M&E"
    MUTESOLO 1 0 0
    <ITEM
      POSITION 0
      LENGTH 10
      NAME en_instrumental.flac
      <SOURCE FLAC
        FILE "/tmp/en_instrumental.flac"
      >
    >
  >
  <TRACK {CCCC}
    NAME "ES VOX"
    MUTESOLO 0 0 0
    <VOLENV2
      PT 0 1 0
      PT 5 2 0
    >
    <FXCHAIN
      BYPASS 0 0 0
      <JS utility/volume ""
        -0.8149 0 - -
      >
      WAK 0 0
    >
    <ITEM
      POSITION 0
      LENGTH 3
      NAME es_vocals.flac
      <SOURCE FLAC
        FILE "/tmp/es_vocals.flac"
      >
    >
    <ITEM
      POSITION 3
      LENGTH 4
      NAME parche
      <SOURCE WAVE
        FILE "/tmp/speech.wav"
      >
    >
  >
  <TRACK {DDDD}
    NAME "ES M&E"
    MUTESOLO 0 0 0
  >
  <TRACK {EEEE}
    NAME "Ayudas"
    MUTESOLO 0 0 0
    <ITEM
      POSITION 1
      LENGTH 2
      NAME extra
      <SOURCE WAVE
        FILE "/tmp/extra.wav"
      >
    >
  >
>
"""


def _plan(tmp_path: Path) -> RenderCopyPlan:
    return RenderCopyPlan(
        output_file=tmp_path / "es_reconstructed.flac",
        mute=frozenset({"EN VOX", "ES M&E"}),
        unmute=frozenset({"ES VOX", "EN M&E"}),
    )


def test_render_destination_is_forced_not_inherited(tmp_path):
    """El caso que motiva todo: el usuario dejo el dialogo de Render
    apuntando a otro fichero.
    """
    out = derive_render_project(_EDITED, _plan(tmp_path))
    assert 'RENDER_FILE "%s"' % (tmp_path / "es_reconstructed.flac") in out
    assert "hudson_risa" not in out


def test_render_format_is_forced_to_the_pipeline_settings(tmp_path):
    out = derive_render_project(_EDITED, _plan(tmp_path))
    assert "RENDER_FMT 0 2 0" in out and "RENDER_FMT 0 1 0" not in out
    assert "RENDER_RANGE 1 0 0 0 1000" in out and "RENDER_RANGE 2 0 0 0 1000" not in out
    assert "RENDER_DITHER 0" in out and "RENDER_DITHER 1" not in out
    assert RENDER_CFG_FLAC16 in out
    assert "QWxnbwBWaWVqbw==" not in out  # el cfg viejo se sustituye entero


def test_mutes_are_forced_in_both_directions(tmp_path):
    """EN VOX estaba sonando y debe callar; EN M&E estaba muteado (una
    escucha suelta que se quedo asi) y debe sonar.
    """
    tracks = read_tracks(parse_rpp(derive_render_project(_EDITED, _plan(tmp_path))))

    def muted(name):
        block = derive_render_project(_EDITED, _plan(tmp_path)).split(f'NAME "{name}"')[1]
        return block.split("MUTESOLO ")[1].split()[0] == "1"

    assert muted("EN VOX")
    assert muted("ES M&E")
    assert not muted("EN M&E")
    assert not muted("ES VOX")
    assert find_track(tracks, "ES VOX") is not None


def test_a_track_the_user_added_survives_and_stays_audible(tmp_path):
    """Justo lo que uno espera al crear una pista de apoyo: que entre en
    el render. Solo se tocan las pistas nombradas en el plan.
    """
    out = derive_render_project(_EDITED, _plan(tmp_path))
    tracks = read_tracks(parse_rpp(out))
    extra = find_track(tracks, "Ayudas")
    assert extra is not None
    assert len(extra.items) == 1
    assert extra.items[0].source_file == Path("/tmp/extra.wav")
    assert not extra.items[0].muted
    assert out.split('NAME "Ayudas"')[1].split("MUTESOLO ")[1].split()[0] == "0"


def test_manual_edits_are_preserved_verbatim(tmp_path):
    """Items, envolvente y FX pasan tal cual: es una copia, no una
    regeneracion.
    """
    tracks = read_tracks(parse_rpp(derive_render_project(_EDITED, _plan(tmp_path))))
    es = find_track(tracks, "ES VOX")
    assert [i.source_file.name for i in es.items] == ["es_vocals.flac", "speech.wav"]
    assert es.vol_points == ((0.0, 1.0), (5.0, 2.0))
    assert es.js_volume_db == pytest.approx(-0.8149)


def test_only_the_forced_lines_change(tmp_path):
    """Red de seguridad: nada mas se toca. Si esto se rompe, alguien ha
    metido una transformacion de mas.
    """
    before = _EDITED.splitlines()
    after = derive_render_project(_EDITED, _plan(tmp_path)).splitlines()
    assert len(before) == len(after)
    changed = {b.strip().split(" ", 1)[0] for b, a in zip(before, after) if b != a}
    assert changed <= {"RENDER_FILE", "RENDER_FMT", "RENDER_RANGE", "RENDER_DITHER",
                       "MUTESOLO", "QWxnbwBWaWVqbw=="}


def test_missing_track_is_a_clear_error(tmp_path):
    plan = RenderCopyPlan(
        output_file=tmp_path / "out.flac",
        mute=frozenset({"NO EXISTE"}), unmute=frozenset(),
    )
    with pytest.raises(RenderProjectError, match="NO EXISTE"):
        derive_render_project(_EDITED, plan)


def test_project_without_render_file_is_a_clear_error(tmp_path):
    text = _EDITED.replace('  RENDER_FILE "/Users/x/Movies/s03e01/02_finals/hudson_risa.flac"\n', "")
    with pytest.raises(RenderProjectError, match="RENDER_FILE"):
        derive_render_project(text, _plan(tmp_path))


def test_write_render_project_round_trips_through_disk(tmp_path):
    edited = tmp_path / "s01e01.RPP"
    edited.write_text(_EDITED)
    render = tmp_path / "s01e01_render.RPP"
    write_render_project(edited, render, _plan(tmp_path))
    assert render.exists()
    tracks = read_tracks(parse_rpp(render.read_text()))
    assert [t.name for t in tracks] == ["EN VOX", "EN M&E", "ES VOX", "ES M&E", "Ayudas"]
