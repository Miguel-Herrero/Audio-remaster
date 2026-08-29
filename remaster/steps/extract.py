"""Paso 1: extraer el audio de cada fuente de video a FLAC.

Cubre a la vez lo que en `automation-steps` eran dos pasos separados
("Extraction" y "Audio Compression"): el EN/JA del mux final es
exactamente este mismo fichero, asi que se extrae una unica vez.

Profundidad de bits: no se fuerza ningun `-sample_fmt`. El encoder FLAC de
ffmpeg negocia el formato con el decodificador de origen, y eso ya produce
el resultado correcto sin necesidad de logica extra:
  - AC3 (DVD, lossy) decodifica a float -> FLAC sale a 24 bit.
  - PCM s16le (Blu-ray) ya es 16 bit -> FLAC sale a 16 bit.
Verificado contra los ficheros reales de s02e06: es_source.flac es 24-bit,
en_source.flac/ja_source.flac son 16-bit, ambos producidos por el mismo
comando sin flags de profundidad.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult
from ..probe import require_binary
from ..sources import AudioSource, ClassifiedSources
from ..toolinfo import ffmpeg_version


class ExtractError(RuntimeError):
    pass


def _run_ffmpeg_extract(input_path: Path, stream_index: int, output_path: Path, ffmpeg_path: str) -> None:
    ffmpeg = require_binary("ffmpeg", ffmpeg_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-map", f"0:{stream_index}",
        "-c:a", "flac",
        "-compression_level", "8",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ExtractError(
            f"ffmpeg fallo extrayendo stream {stream_index} de {input_path.name}:\n"
            f"{result.stderr.strip()}"
        )


@dataclass(frozen=True)
class ExtractOutcome:
    es: StepResult
    en: StepResult
    ja: StepResult | None  # None si el Blu-ray no trae pista japonesa


def extract_all(
    sources: ClassifiedSources,
    layout: EpisodeLayout,
    manifest: Manifest,
    ffmpeg_path: str = "ffmpeg",
    force: bool = False,
) -> ExtractOutcome:
    tool_versions = {"ffmpeg": ffmpeg_version(ffmpeg_path)}

    def _extract_one(label: str, audio: AudioSource, output: Path) -> StepResult:
        params = {"stream_index": audio.stream_index, "source_codec": audio.codec_name}
        return manifest.run_step(
            step_name=f"extract:{label}",
            input_paths=[audio.path],
            params=params,
            output_paths=[output],
            tool_versions=tool_versions,
            fn=lambda: _run_ffmpeg_extract(audio.path, audio.stream_index, output, ffmpeg_path),
            force=force,
        )

    es_result = _extract_one("es", sources.es, layout.es_source)
    en_result = _extract_one("en", sources.en, layout.en_source)
    ja_result = _extract_one("ja", sources.ja, layout.ja_source) if sources.ja else None

    return ExtractOutcome(es=es_result, en=en_result, ja=ja_result)
