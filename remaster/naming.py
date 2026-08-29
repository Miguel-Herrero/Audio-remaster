"""Nombre del fichero final: mismo formato que ya usas a mano, p.ej.

    Las aventuras de Sherlock Holmes (1984) S02E06 El problema final -
    [1080p AVC] [FLAC ENG SPA JPN] [Subs ENG JPN].mkv

Separado de mux.py/remux4k.py porque lo usan los dos (el mux normal y el
remux 4K generan cada uno su propio nombre, con su propia resolucion/codec
de video y sus propios idiomas de subtitulos).
"""

from __future__ import annotations

_CODEC_LABELS = {
    "h264": "AVC",
    "avc1": "AVC",
    "hevc": "HEVC",
    "h265": "HEVC",
    "vp9": "VP9",
    "vp09": "VP9",
    "vp8": "VP8",
    "av1": "AV1",
    "mpeg2video": "MPEG2",
}


def codec_label(codec_name: str) -> str:
    return _CODEC_LABELS.get(codec_name.lower(), codec_name.upper())


def build_final_filename(
    series_title: str,
    code: str,
    episode_title: str,
    video_height: int,
    video_codec: str,
    audio_langs: list[str],
    sub_langs: list[str],
) -> str:
    """`audio_langs`/`sub_langs` en el orden en que deben aparecer en el
    nombre (normalmente eng, spa, jpn) — ya filtrados a los presentes.
    """
    parts = [f"{series_title} {code.upper()} {episode_title} -", f"[{video_height}p {codec_label(video_codec)}]"]
    if audio_langs:
        parts.append(f"[FLAC {' '.join(l.upper() for l in audio_langs)}]")
    if sub_langs:
        parts.append(f"[Subs {' '.join(l.upper() for l in sub_langs)}]")
    return " ".join(parts) + ".mkv"
