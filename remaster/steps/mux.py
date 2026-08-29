"""Paso 9: mux final a MKV. Video + subtitulos del Blu-ray, audio ES
(reconstruido, default) + EN (original) + JA (si existe).

El orden de ficheros en el comando `mkvmerge` es fijo por diseño:
  0 = Blu-ray (video + subs), 1 = ES, 2 = EN, 3 = JA (si existe).
Con eso, `--track-order` deja de ser aritmetica fragil: no hay que
calcular indices que se descuadran cuando falta el japones (el bug del
script viejo) — cada fichero tiene siempre la misma posicion, este o no
JA presente. El orden de pistas resultante (Video, ES, EN, JA, Subs)
replica el que ya se usaba a mano en SH1984/v1.0.1.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..manifest import Manifest, StepResult
from ..probe import ProbeError, mkvmerge_info, require_binary
from ..profile import Profile
from ..toolinfo import mkvmerge_version

# mkvmerge: 0 = ok, 1 = ok con warnings, >=2 = error real.
MKVMERGE_ERROR_EXIT_CODE = 2


class MuxError(RuntimeError):
    pass


def subtitle_track_ids(bluray_container: Path, mkvmerge_path: str) -> list[int]:
    info = mkvmerge_info(bluray_container, mkvmerge_path)
    return [t["id"] for t in info.get("tracks", []) if t["type"] == "subtitles"]


# Orden en el que se listan los idiomas en el nombre de fichero (ver
# remaster/naming.py) — no es el orden de pista en el MKV, es una
# convencion de nombrado fija.
_LANGUAGE_DISPLAY_ORDER = ("eng", "spa", "jpn")


def subtitle_languages(bluray_container: Path, mkvmerge_path: str) -> list[str]:
    """Idiomas de subtitulo presentes en el Blu-ray, unicos, en el orden
    de `_LANGUAGE_DISPLAY_ORDER` (los que no esten en esa lista van detras,
    ordenados alfabeticamente).
    """
    info = mkvmerge_info(bluray_container, mkvmerge_path)
    present = {
        t["properties"].get("language", "und")
        for t in info.get("tracks", []) if t["type"] == "subtitles"
    }
    ordered = [lang for lang in _LANGUAGE_DISPLAY_ORDER if lang in present]
    ordered += sorted(present - set(_LANGUAGE_DISPLAY_ORDER))
    return ordered


def build_mux_command(
    mkvmerge_path: str,
    bluray_container: Path,
    en_flac: Path,
    es_mix: Path,
    ja_flac: Path | None,
    subtitle_ids: list[int],
    output_file: Path,
    title: str | None,
    profile: Profile,
) -> list[str]:
    cmd = [mkvmerge_path, "-o", str(output_file)]
    if title:
        cmd += ["--title", title]

    # file 0: Blu-ray — solo video + subtitulos.
    cmd += ["--video-tracks", "0", "--no-audio", "--disable-track-statistics-tags"]
    if subtitle_ids:
        cmd += ["--subtitle-tracks", ",".join(str(i) for i in subtitle_ids)]
        for sid in subtitle_ids:
            cmd += ["--forced-display-flag", f"{sid}:0", "--default-track-flag", f"{sid}:0"]
    cmd.append(str(bluray_container))

    # file 1: ES (reconstruido, default).
    cmd += [
        "--language", f"0:{profile.languages.es}",
        "--default-track-flag", "0:1",
        "--disable-track-statistics-tags",
        "--track-name", f"0:{profile.mux.es_track_name}",
        str(es_mix),
    ]

    # file 2: EN (original, no default).
    cmd += [
        "--language", f"0:{profile.languages.en}",
        "--default-track-flag", "0:0",
        "--disable-track-statistics-tags",
        "--original-flag", "0:1",
        str(en_flac),
    ]

    track_order = ["0:0", "1:0", "2:0"]

    # file 3 (opcional): JA.
    if ja_flac is not None:
        cmd += [
            "--language", f"0:{profile.languages.ja}",
            "--default-track-flag", "0:0",
            "--disable-track-statistics-tags",
            str(ja_flac),
        ]
        track_order.append("3:0")

    track_order += [f"0:{sid}" for sid in subtitle_ids]
    cmd += ["--track-order", ",".join(track_order)]
    return cmd


def mux_step(
    bluray_container: Path,
    en_flac: Path,
    es_mix: Path,
    ja_flac: Path | None,
    output_file: Path,
    title: str | None,
    profile: Profile,
    manifest: Manifest,
    mkvmerge_path: str = "mkvmerge",
    force: bool = False,
) -> StepResult:
    mkvmerge = require_binary("mkvmerge", mkvmerge_path)
    try:
        subtitle_ids = subtitle_track_ids(bluray_container, mkvmerge)
    except ProbeError as exc:
        raise MuxError(str(exc)) from exc

    params = {
        "title": title, "subtitle_ids": subtitle_ids, "has_ja": ja_flac is not None,
        "es_track_name": profile.mux.es_track_name,
        "languages": profile.languages.__dict__,
    }
    tool_versions = {"mkvmerge": mkvmerge_version(mkvmerge)}
    inputs = [bluray_container, en_flac, es_mix] + ([ja_flac] if ja_flac else [])

    def fn() -> None:
        cmd = build_mux_command(mkvmerge, bluray_container, en_flac, es_mix, ja_flac, subtitle_ids, output_file, title, profile)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode >= MKVMERGE_ERROR_EXIT_CODE:
            raise MuxError(f"mkvmerge fallo (codigo {result.returncode}):\n{result.stderr.strip()}\n{result.stdout.strip()}")

    return manifest.run_step(
        step_name="mux", input_paths=inputs, params=params,
        output_paths=[output_file], tool_versions=tool_versions, fn=fn, force=force,
    )
