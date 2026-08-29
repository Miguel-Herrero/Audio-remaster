"""Tests de clasificacion de fuentes (remaster/sources.py).

Los numeros (fps, duraciones, codecs) reproducen exactamente lo que
`ffprobe` reporta sobre los ficheros reales de ~/Movies/s02e06 y
~/Movies/s03e01, capturado durante el diseño del pipeline. No se leen
ficheros reales en el test: se crean ficheros vacios con el nombre
correcto (para que `Path.iterdir()` los encuentre) y se parchea
`probe_file` para devolver un ProbeResult fabricado a mano.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from remaster import sources as sources_mod
from remaster.probe import ProbeResult, Stream
from remaster.sources import (
    SourceOverrides,
    SourcesError,
    classify_sources,
)

FPS_2397 = Fraction(24000, 1001)
FPS_25 = Fraction(25, 1)


def _dvd_probe(path: Path) -> ProbeResult:
    return ProbeResult(
        path=path, duration=3081.96,
        streams=(
            Stream(0, "video", "mpeg2video", language="eng", width=720, height=576, frame_rate=FPS_25),
            Stream(1, "audio", "ac3", language="spa", channels=2, sample_rate=48000),
        ),
    )


def _bluray_probe(path: Path, with_jpn: bool = True) -> ProbeResult:
    streams = [
        Stream(0, "video", "h264", language="eng", width=1920, height=1080, frame_rate=FPS_2397),
        Stream(1, "audio", "pcm_s16le", language="eng", channels=2, sample_rate=48000, bits_per_raw_sample=16),
    ]
    if with_jpn:
        streams.append(Stream(2, "audio", "pcm_s16le", language="jpn", channels=2, sample_rate=48000, bits_per_raw_sample=16))
        streams += [
            Stream(3, "subtitle", "hdmv_pgs_subtitle", language="jpn"),
            Stream(4, "subtitle", "hdmv_pgs_subtitle", language="jpn"),
            Stream(5, "subtitle", "hdmv_pgs_subtitle", language="eng"),
        ]
    return ProbeResult(path=path, duration=3210.711, streams=tuple(streams))


def _remaster4k_probe(path: Path) -> ProbeResult:
    return ProbeResult(
        path=path, duration=3210.768,
        streams=(
            Stream(0, "video", "vp9", language="eng", width=2880, height=2160, frame_rate=FPS_2397),
            Stream(1, "audio", "opus", language="eng", channels=2, sample_rate=48000),
        ),
    )


DVD_NAME = "A1_t00.mkv"
BLURAY_NAME = "Sherlock Holmes.s02e06.The Final Problem.mkv"
REMASTER_NAME = "The Adventures of Sherlock Holmes (1984) - S02E06 - 4K AI Remaster.webm"
REMASTER_VTT = "The Adventures of Sherlock Holmes (1984) - S02E06 - 4K AI Remaster.en-GB.vtt"


def _patch_probe(monkeypatch, probes_by_name: dict[str, ProbeResult]) -> None:
    def fake_probe_file(path: Path, ffprobe_path: str = "ffprobe") -> ProbeResult:
        return probes_by_name[path.name]
    monkeypatch.setattr(sources_mod, "probe_file", fake_probe_file)


def _touch(dir_: Path, *names: str) -> None:
    for n in names:
        (dir_ / n).touch()


def test_classifies_full_episode_with_4k(tmp_path, monkeypatch):
    src = tmp_path / "00_sources"
    src.mkdir()
    _touch(src, DVD_NAME, BLURAY_NAME, REMASTER_NAME)
    (src / REMASTER_VTT).touch()

    _patch_probe(monkeypatch, {
        DVD_NAME: _dvd_probe(src / DVD_NAME),
        BLURAY_NAME: _bluray_probe(src / BLURAY_NAME),
        REMASTER_NAME: _remaster4k_probe(src / REMASTER_NAME),
    })

    result = classify_sources(src)

    assert result.dvd_container.name == DVD_NAME
    assert result.es.stream_index == 1
    assert result.bluray_container.name == BLURAY_NAME
    assert result.en.stream_index == 1
    assert result.ja is not None and result.ja.stream_index == 2
    assert result.target_fps == FPS_2397
    assert result.dvd_video is not None and result.dvd_video.frame_rate == FPS_25
    assert result.remaster4k is not None
    assert result.remaster4k.video.path.name == REMASTER_NAME
    assert result.remaster4k.subtitle is not None
    assert result.remaster4k.subtitle.name == REMASTER_VTT


def test_classifies_without_4k(tmp_path, monkeypatch):
    """Caso s03e01: sin remaster 4K, el pipeline no debe fallar ni pedirlo."""
    src = tmp_path / "00_sources"
    src.mkdir()
    _touch(src, DVD_NAME, BLURAY_NAME)

    _patch_probe(monkeypatch, {
        DVD_NAME: _dvd_probe(src / DVD_NAME),
        BLURAY_NAME: _bluray_probe(src / BLURAY_NAME),
    })

    result = classify_sources(src)

    assert result.remaster4k is None
    assert result.ja is not None


def test_bluray_without_japanese_track_is_still_valid(tmp_path, monkeypatch):
    src = tmp_path / "00_sources"
    src.mkdir()
    _touch(src, DVD_NAME, BLURAY_NAME)

    _patch_probe(monkeypatch, {
        DVD_NAME: _dvd_probe(src / DVD_NAME),
        BLURAY_NAME: _bluray_probe(src / BLURAY_NAME, with_jpn=False),
    })

    result = classify_sources(src)

    assert result.ja is None
    assert result.en.stream_index == 1


def test_4k_never_supplies_audio_even_if_it_is_the_only_english_track(tmp_path, monkeypatch):
    """El 4K es video_only por rol, no por heuristica de calidad: aunque
    su audio estuviera (incorrectamente) marcado como lossless, no debe
    poder colarse como Blu-ray.
    """
    src = tmp_path / "00_sources"
    src.mkdir()
    _touch(src, DVD_NAME, BLURAY_NAME, REMASTER_NAME)

    fake_4k_with_lossless_tag = ProbeResult(
        path=src / REMASTER_NAME, duration=3210.768,
        streams=(
            Stream(0, "video", "vp9", language="eng", width=2880, height=2160, frame_rate=FPS_2397),
            # Etiquetado (erroneamente, a proposito del test) como pcm_s16le.
            Stream(1, "audio", "pcm_s16le", language="eng", channels=2, sample_rate=48000, bits_per_raw_sample=16),
        ),
    )
    _patch_probe(monkeypatch, {
        DVD_NAME: _dvd_probe(src / DVD_NAME),
        BLURAY_NAME: _bluray_probe(src / BLURAY_NAME),
        REMASTER_NAME: fake_4k_with_lossless_tag,
    })

    result = classify_sources(src)

    # Debe seguir eligiendo el Blu-ray real, no el 4K, pese a que este
    # ultimo tambien "cualificaria" por codec.
    assert result.bluray_container.name == BLURAY_NAME
    assert result.remaster4k is not None
    assert result.remaster4k.video.path.name == REMASTER_NAME


def test_missing_dvd_raises_clear_error(tmp_path, monkeypatch):
    src = tmp_path / "00_sources"
    src.mkdir()
    _touch(src, BLURAY_NAME)
    _patch_probe(monkeypatch, {BLURAY_NAME: _bluray_probe(src / BLURAY_NAME)})

    with pytest.raises(SourcesError, match="DVD castellano"):
        classify_sources(src)


def test_missing_bluray_raises_clear_error(tmp_path, monkeypatch):
    src = tmp_path / "00_sources"
    src.mkdir()
    _touch(src, DVD_NAME)
    _patch_probe(monkeypatch, {DVD_NAME: _dvd_probe(src / DVD_NAME)})

    with pytest.raises(SourcesError, match="Blu-ray"):
        classify_sources(src)


def test_ambiguous_4k_requires_override(tmp_path, monkeypatch):
    src = tmp_path / "00_sources"
    src.mkdir()
    second_4k = "another 4K remaster.webm"
    _touch(src, DVD_NAME, BLURAY_NAME, REMASTER_NAME, second_4k)

    _patch_probe(monkeypatch, {
        DVD_NAME: _dvd_probe(src / DVD_NAME),
        BLURAY_NAME: _bluray_probe(src / BLURAY_NAME),
        REMASTER_NAME: _remaster4k_probe(src / REMASTER_NAME),
        second_4k: _remaster4k_probe(src / second_4k),
    })

    with pytest.raises(SourcesError, match="remaster 4K"):
        classify_sources(src)


def test_overrides_bypass_autodetection(tmp_path, monkeypatch):
    """El DVD tiene el castellano sin etiquetar ('und'): sin override, la
    autodeteccion no lo encuentra. Con --es-source/--es-track si.
    """
    src = tmp_path / "00_sources"
    src.mkdir()
    weird_name = "rip_raro.mkv"
    _touch(src, weird_name, BLURAY_NAME)
    weird_probe = ProbeResult(
        path=src / weird_name, duration=100.0,
        streams=(
            Stream(0, "video", "mpeg2video", width=720, height=576, frame_rate=FPS_25),
            Stream(1, "audio", "dts", language="und", channels=2),
        ),
    )
    _patch_probe(monkeypatch, {
        weird_name: weird_probe,
        BLURAY_NAME: _bluray_probe(src / BLURAY_NAME),
    })

    with pytest.raises(SourcesError, match="DVD castellano"):
        classify_sources(src)

    result = classify_sources(
        src,
        overrides=SourceOverrides(es_source=src / weird_name, es_track=1),
    )
    assert result.dvd_container.name == weird_name
    assert result.es.stream_index == 1
    assert result.bluray_container.name == BLURAY_NAME
