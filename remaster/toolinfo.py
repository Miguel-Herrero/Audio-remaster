"""Version efectiva de las herramientas externas, para dejar constancia en
el manifiesto de con que binario se produjo cada salida.
"""

from __future__ import annotations

import subprocess

from .probe import require_binary


def _first_line(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        text = (result.stdout or result.stderr or "").strip().splitlines()
        return text[0] if text else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def ffmpeg_version(ffmpeg_path: str = "ffmpeg") -> str:
    ffmpeg = require_binary("ffmpeg", ffmpeg_path)
    return _first_line([ffmpeg, "-version"])


def ffprobe_version(ffprobe_path: str = "ffprobe") -> str:
    ffprobe = require_binary("ffprobe", ffprobe_path)
    return _first_line([ffprobe, "-version"])


def mkvmerge_version(mkvmerge_path: str = "mkvmerge") -> str:
    mkvmerge = require_binary("mkvmerge", mkvmerge_path)
    return _first_line([mkvmerge, "--version"])
