"""Tests del nombre de fichero final (remaster/naming.py).

Los dos casos exactos son los que dio el usuario como referencia real:

  Las aventuras de Sherlock Holmes (1984) S02E06 El problema final -
  [1080p AVC] [FLAC ENG SPA JPN] [Subs ENG JPN].mkv

  Las aventuras de Sherlock Holmes (1984) S02E06 El problema final -
  [2160p VP9] [FLAC ENG SPA JPN] [Subs ENG].mkv

Lo que va delante del guion se pasa entero: aqui no se interpola nada.
"""

from __future__ import annotations

from remaster.naming import build_final_filename, codec_label

NAME = "Las aventuras de Sherlock Holmes (1984) S02E06 El problema final"


def test_matches_real_main_mux_filename():
    name = build_final_filename(
        NAME,
        video_height=1080, video_codec="h264",
        audio_langs=["eng", "spa", "jpn"], sub_langs=["eng", "jpn"],
    )
    assert name == (
        "Las aventuras de Sherlock Holmes (1984) S02E06 El problema final - "
        "[1080p AVC] [FLAC ENG SPA JPN] [Subs ENG JPN].mkv"
    )


def test_matches_real_4k_remux_filename():
    name = build_final_filename(
        NAME,
        video_height=2160, video_codec="vp9",
        audio_langs=["eng", "spa", "jpn"], sub_langs=["eng"],
    )
    assert name == (
        "Las aventuras de Sherlock Holmes (1984) S02E06 El problema final - "
        "[2160p VP9] [FLAC ENG SPA JPN] [Subs ENG].mkv"
    )


def test_name_is_used_verbatim():
    """Sin serie, sin SxxExx, sin nada: vale igual para una pelicula."""
    name = build_final_filename(
        "El tercer hombre (1949)", 1080, "h264", ["eng", "spa"], ["spa"],
    )
    assert name == "El tercer hombre (1949) - [1080p AVC] [FLAC ENG SPA] [Subs SPA].mkv"


def test_surrounding_whitespace_is_trimmed():
    """Pegar el nombre en la UI deja espacios sueltos con facilidad."""
    name = build_final_filename("  Serie X S01E01  ", 1080, "h264", ["eng"], [])
    assert name.startswith("Serie X S01E01 - ")


def test_unknown_codec_falls_back_to_uppercase():
    assert codec_label("theora") == "THEORA"


def test_no_subs_omits_the_subs_tag():
    name = build_final_filename(NAME, 1080, "h264", ["eng", "spa"], [])
    assert "[Subs" not in name
    assert name.endswith("[FLAC ENG SPA].mkv")
