"""Paso 10 (opcional): remux con el video del remaster 4K. Solo aporta
video (nunca audio, ver sources.py) mas su subtitulo externo si existe.

Si la carpeta del episodio no tiene fuente 4K, este paso simplemente no
se invoca — no es un error, es el caso normal de un episodio sin
remaster (p.ej. s03e01 en el momento de escribir esto).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..manifest import Manifest, StepResult
from ..probe import mkvmerge_info, require_binary
from ..toolinfo import mkvmerge_version

MKVMERGE_ERROR_EXIT_CODE = 2


class Remux4KError(RuntimeError):
    pass


def container_title(mkv_path: Path, mkvmerge_path: str) -> str | None:
    info = mkvmerge_info(mkv_path, mkvmerge_path)
    return info.get("container", {}).get("properties", {}).get("title")


def _base_track_ids(base_mkv: Path, mkvmerge_path: str) -> tuple[list[int], list[int]]:
    info = mkvmerge_info(base_mkv, mkvmerge_path)
    audio_ids = [t["id"] for t in info.get("tracks", []) if t["type"] == "audio"]
    sub_ids = [t["id"] for t in info.get("tracks", []) if t["type"] == "subtitles"]
    return audio_ids, sub_ids


def rewrite_title_4k(base_title: str | None) -> str | None:
    if not base_title:
        return None
    if "(1984)" in base_title:
        return base_title.replace("(1984)", "(1984) [4K]")
    return f"{base_title} [4K]"


def build_remux_command(
    mkvmerge_path: str,
    base_mkv: Path,
    remaster_video: Path,
    video_stream_index: int,
    output_file: Path,
    audio_ids: list[int],
    sub_ids: list[int],
    external_subtitle: Path | None,
    base_title: str | None,
) -> list[str]:
    cmd = [mkvmerge_path, "-o", str(output_file)]
    title = rewrite_title_4k(base_title)
    if title:
        cmd += ["--title", title]

    # file 0: solo el video del remaster 4K.
    cmd += ["--video-tracks", str(video_stream_index), "--no-audio", "--no-subtitles", str(remaster_video)]

    # file 1: audio + subtitulos del mux ya hecho (nunca su video).
    cmd += ["--no-video"]
    if audio_ids:
        cmd += ["--audio-tracks", ",".join(str(i) for i in audio_ids)]
    if sub_ids:
        cmd += ["--subtitle-tracks", ",".join(str(i) for i in sub_ids)]
    cmd.append(str(base_mkv))

    track_order = [f"0:{video_stream_index}"]
    track_order += [f"1:{i}" for i in audio_ids]
    track_order += [f"1:{i}" for i in sub_ids]

    # file 2 (opcional): subtitulo externo del remaster.
    if external_subtitle is not None:
        cmd += [
            "--language", "0:eng",
            "--track-name", "0:English (4K)",
            "--default-track-flag", "0:0",
            "--forced-display-flag", "0:0",
            str(external_subtitle),
        ]
        track_order.append("2:0")

    cmd += ["--track-order", ",".join(track_order)]
    return cmd


def remux4k_step(
    base_mkv: Path,
    remaster_video: Path,
    video_stream_index: int,
    external_subtitle: Path | None,
    output_file: Path,
    manifest: Manifest,
    mkvmerge_path: str = "mkvmerge",
    force: bool = False,
) -> StepResult:
    mkvmerge = require_binary("mkvmerge", mkvmerge_path)
    audio_ids, sub_ids = _base_track_ids(base_mkv, mkvmerge)
    base_title = container_title(base_mkv, mkvmerge)

    params = {
        "video_stream_index": video_stream_index, "audio_ids": audio_ids, "sub_ids": sub_ids,
        "has_external_subtitle": external_subtitle is not None, "base_title": base_title,
    }
    inputs = [base_mkv, remaster_video] + ([external_subtitle] if external_subtitle else [])
    tool_versions = {"mkvmerge": mkvmerge_version(mkvmerge)}

    def fn() -> None:
        cmd = build_remux_command(
            mkvmerge, base_mkv, remaster_video, video_stream_index, output_file,
            audio_ids, sub_ids, external_subtitle, base_title,
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode >= MKVMERGE_ERROR_EXIT_CODE:
            raise Remux4KError(f"mkvmerge fallo (codigo {result.returncode}):\n{result.stderr.strip()}\n{result.stdout.strip()}")

    return manifest.run_step(
        step_name="remux4k", input_paths=inputs, params=params,
        output_paths=[output_file], tool_versions=tool_versions, fn=fn, force=force,
    )
