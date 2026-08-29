"""Test de snapshot del generador de .RPP (remaster/reaper/rpp.py).

No compara contra un fichero de referencia externo: genera el mismo
proyecto dos veces y exige bytes identicos (los GUIDs son deterministas,
ver `deterministic_guid`), y comprueba que las piezas verificadas contra
un proyecto real de REAPER (blob de ReaEQ, RENDER_CFG, JSFX utility/volume)
aparecen literalmente en la salida.
"""

from __future__ import annotations

from pathlib import Path

from remaster.reaper.rpp import (
    REAEQ_HOLMES_STATE_LINES,
    REAEQ_PRESET_NAME,
    REAFIR_DEFAULT_STATE_LINES,
    RENDER_CFG_FLAC16,
    Item,
    Project,
    RenderSettings,
    Track,
    VolPoint,
    build_project,
    deterministic_guid,
    js_volume_fx_lines,
    reaeq_holmes_fx_lines,
    reafir_default_fx_lines,
)


def _sample_project() -> Project:
    es_vox = Track(
        key="ES_VOX", name="ES VOX",
        items=(Item(position=1.2, length=10.0, name="es_vocals.flac", source_file=Path("/tmp/es_vocals.flac")),),
        vol_points=(VolPoint(0.0, 1.0), VolPoint(5.0, 0.7), VolPoint(10.0, 1.0)),
        fx_lines=js_volume_fx_lines(1.6, "ep01", "ES_VOX") + reaeq_holmes_fx_lines("ep01", "ES_VOX")
        + reafir_default_fx_lines("ep01", "ES_VOX"),
    )
    en_me = Track(
        key="EN_ME", name="EN M&E",
        items=(Item(position=0.0, length=10.0, name="en_instrumental.flac", source_file=Path("/tmp/en_instrumental.flac")),),
    )
    video = Track(
        key="VIDEO", name="VIDEO (Reference)", mainsend=False,
        items=(Item(position=0.0, length=10.0, name="video.mkv", source_file=Path("/tmp/video.mkv"), source_kind="VIDEO"),),
    )
    return Project(
        episode_code="ep01",
        tracks=(video, en_me, es_vox),
        render=RenderSettings(output_file=Path("/tmp/out.flac")),
    )


def test_same_input_produces_byte_identical_output():
    text1 = build_project(_sample_project())
    text2 = build_project(_sample_project())
    assert text1 == text2


def test_different_episode_code_changes_guids():
    p1 = _sample_project()
    p2 = Project(episode_code="ep02", tracks=p1.tracks, render=p1.render)
    assert build_project(p1) != build_project(p2)


def test_output_contains_verified_reaeq_blob():
    text = build_project(_sample_project())
    assert f"PRESETNAME {REAEQ_PRESET_NAME}" in text
    for line in REAEQ_HOLMES_STATE_LINES:
        assert line in text


def test_output_contains_verified_reafir_blob_bypassed():
    text = build_project(_sample_project())
    assert 'VST: ReaFir (FFT EQ+Dynamics Processor) (Cockos)" reafir.vst.dylib' in text
    for line in REAFIR_DEFAULT_STATE_LINES:
        assert line in text
    reafir_block_start = text.index('VST: ReaFir')
    bypass_line = text[:reafir_block_start].rsplit("BYPASS", 1)[-1]
    assert bypass_line.strip().startswith("1 0 0")


def test_output_contains_verified_render_cfg():
    text = build_project(_sample_project())
    assert RENDER_CFG_FLAC16 in text


def test_js_volume_state_has_64_tokens():
    fx = js_volume_fx_lines(2.5, "ep01", "ES_VOX")
    state_line = next(l for l in fx if l.strip().startswith("2.5"))
    assert len(state_line.split()) == 64


def test_muted_track_serializes_mutesolo_flag():
    from dataclasses import replace
    p = _sample_project()
    muted_en_me = replace(p.tracks[1], muted=True)
    p2 = Project(episode_code=p.episode_code, tracks=(p.tracks[0], muted_en_me, p.tracks[2]), render=p.render)
    text = build_project(p2)
    assert "MUTESOLO 1 0 0" in text


def test_deterministic_guid_is_stable_and_well_formed():
    g1 = deterministic_guid("seed-a")
    g2 = deterministic_guid("seed-a")
    g3 = deterministic_guid("seed-b")
    assert g1 == g2
    assert g1 != g3
    assert g1.startswith("{") and g1.endswith("}")
    assert len(g1) == 38  # {8-4-4-4-12}
