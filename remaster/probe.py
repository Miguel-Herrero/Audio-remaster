"""Wrappers deterministas sobre ffprobe/mkvmerge: convierte un contenedor en
un modelo de streams tipado. Nada de aqui adivina roles (eso vive en
sources.py) — este modulo solo describe con precision lo que hay dentro
de un fichero.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path


class ProbeError(RuntimeError):
    """ffprobe/mkvmerge no pudieron analizar el fichero."""


def _parse_frame_rate(value: str | None) -> Fraction | None:
    """'24000/1001' -> Fraction(24000, 1001). '0/0' (sin video) -> None."""
    if not value:
        return None
    try:
        num, _, den = value.partition("/")
        den = den or "1"
        if int(den) == 0:
            return None
        return Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class Stream:
    """Un stream tal como lo reporta ffprobe, con solo los campos que el
    pipeline necesita para clasificar fuentes y construir comandos.
    """

    index: int
    codec_type: str  # "video" | "audio" | "subtitle"
    codec_name: str
    language: str | None = None
    title: str | None = None
    # audio
    channels: int | None = None
    sample_rate: int | None = None
    bits_per_raw_sample: int | None = None
    # video
    width: int | None = None
    height: int | None = None
    frame_rate: Fraction | None = None

    @property
    def is_lossless(self) -> bool:
        """Codecs sin perdida habituales en discos fisicos. No exhaustivo
        a proposito: si aparece algo raro, mejor que falle la clasificacion
        a que se de por buena una fuente lossy en silencio.
        """
        return self.codec_name in {
            "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le",
            "flac", "alac", "truehd", "mlp",
        }


@dataclass(frozen=True)
class ProbeResult:
    path: Path
    duration: float
    streams: tuple[Stream, ...] = field(default_factory=tuple)

    def by_type(self, codec_type: str) -> list[Stream]:
        return [s for s in self.streams if s.codec_type == codec_type]

    @property
    def video(self) -> list[Stream]:
        return self.by_type("video")

    @property
    def audio(self) -> list[Stream]:
        return self.by_type("audio")

    @property
    def subtitles(self) -> list[Stream]:
        return self.by_type("subtitle")


def require_binary(name: str, path: str | None = None) -> str:
    """Resuelve el ejecutable por nombre (PATH) o ruta explicita; falla claro
    si no esta, en vez de dejar que subprocess lance un FileNotFoundError
    críptico mas tarde.
    """
    candidate = path or name
    resolved = shutil.which(candidate) if not Path(candidate).is_absolute() else candidate
    if not resolved or not Path(resolved).exists():
        raise ProbeError(
            f"No se encontro el ejecutable '{name}' (buscado como '{candidate}'). "
            f"Ejecuta 'remaster doctor' para diagnosticar herramientas."
        )
    return resolved


def probe_file(path: Path, ffprobe_path: str = "ffprobe") -> ProbeResult:
    """Analiza un fichero de audio/video con ffprobe y devuelve un
    ProbeResult tipado. Es la unica fuente de verdad sobre que streams
    tiene un fichero: sources.py clasifica a partir de esto, nunca del
    nombre del fichero.
    """
    if not path.exists():
        raise ProbeError(f"No existe: {path}")

    ffprobe = require_binary("ffprobe", ffprobe_path)
    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProbeError(f"ffprobe fallo en {path.name}:\n{result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe devolvio JSON invalido para {path.name}: {exc}") from exc

    streams = []
    for raw in data.get("streams", []):
        tags = raw.get("tags", {}) or {}
        bits = raw.get("bits_per_raw_sample")
        streams.append(Stream(
            index=raw["index"],
            codec_type=raw.get("codec_type", "unknown"),
            codec_name=raw.get("codec_name", "unknown"),
            language=tags.get("language"),
            title=tags.get("title"),
            channels=raw.get("channels"),
            sample_rate=int(raw["sample_rate"]) if raw.get("sample_rate") else None,
            bits_per_raw_sample=int(bits) if bits else None,
            width=raw.get("width"),
            height=raw.get("height"),
            frame_rate=_parse_frame_rate(raw.get("r_frame_rate")),
        ))

    duration_raw = data.get("format", {}).get("duration")
    duration = float(duration_raw) if duration_raw is not None else 0.0

    return ProbeResult(path=path, duration=duration, streams=tuple(streams))


def mkvmerge_info(path: Path, mkvmerge_path: str = "mkvmerge") -> dict:
    """`mkvmerge -J <path>` parseado. Usado por los pasos de mux/remux para
    flags de pista (forced/default) que ffprobe no expone bien.
    """
    mkvmerge = require_binary("mkvmerge", mkvmerge_path)
    cmd = [mkvmerge, "-J", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProbeError(f"mkvmerge -J fallo en {path.name}:\n{result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"mkvmerge devolvio JSON invalido para {path.name}: {exc}") from exc
