"""`remaster doctor`: comprueba (y opcionalmente propone arreglar) las
herramientas externas de las que depende el pipeline. No instala nada por
si mismo — cli.py pide confirmacion antes de ejecutar cualquier `brew`.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REAPER_APP = Path("/Applications/REAPER.app")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix_hint: str | None = None


def _binary_version(binary: str, *args: str) -> str | None:
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=10)
        text = (result.stdout or result.stderr or "").strip().splitlines()
        return text[0] if text else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_binary(name: str, version_args: tuple[str, ...], fix_hint: str, path_hint: str | None = None) -> CheckResult:
    resolved = path_hint if path_hint and Path(path_hint).exists() else shutil.which(name)
    if not resolved:
        return CheckResult(name, ok=False, detail="no encontrado en PATH", fix_hint=fix_hint)
    version = _binary_version(resolved, *version_args) or "version desconocida"
    return CheckResult(name, ok=True, detail=f"{resolved} — {version}")


def check_reaper() -> CheckResult:
    if not REAPER_APP.exists():
        return CheckResult(
            "REAPER.app", ok=False, detail="no instalado en /Applications",
            fix_hint="Descargar de https://www.reaper.fm/download.php (no se instala automaticamente)",
        )
    plist_path = REAPER_APP / "Contents" / "Info.plist"
    version = "version desconocida"
    if plist_path.exists():
        try:
            with plist_path.open("rb") as f:
                plist = plistlib.load(f)
            version = plist.get("CFBundleShortVersionString", version)
        except (OSError, plistlib.InvalidFileException):
            pass
    binary = REAPER_APP / "Contents" / "MacOS" / "REAPER"
    if not binary.exists():
        return CheckResult("REAPER.app", ok=False, detail=f"bundle presente pero falta {binary}")
    return CheckResult("REAPER.app", ok=True, detail=f"{REAPER_APP} — v{version}")


def check_python() -> CheckResult:
    ok = sys.version_info >= (3, 12)
    detail = f"{sys.version.split()[0]} ({sys.executable})"
    return CheckResult("Python", ok=ok, detail=detail,
                        fix_hint=None if ok else "Se requiere Python >= 3.12")


def run_all(
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    mkvmerge_path: str | None = None,
    mkvextract_path: str | None = None,
) -> list[CheckResult]:
    return [
        check_python(),
        check_binary("uv", ("--version",), fix_hint="curl -LsSf https://astral.sh/uv/install.sh | sh"),
        check_binary("ffmpeg", ("-version",), fix_hint="brew install ffmpeg", path_hint=ffmpeg_path),
        check_binary("ffprobe", ("-version",), fix_hint="brew install ffmpeg", path_hint=ffprobe_path),
        check_binary("mkvmerge", ("--version",), fix_hint="brew install mkvtoolnix", path_hint=mkvmerge_path),
        check_binary("mkvextract", ("--version",), fix_hint="brew install mkvtoolnix", path_hint=mkvextract_path),
        check_reaper(),
    ]
