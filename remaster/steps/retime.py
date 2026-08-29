"""Paso 2: convertir el frame rate del audio castellano al del Blu-ray.

El ratio se calcula de los fps reales detectados en el ingest, no de una
constante: `ratio = fps_destino / fps_origen`. Para el caso real del
proyecto, (24000/1001)/25 = 0.95904 exacto — coincide con el valor que
`automation-steps/steps/retime.py` tenia hardcodeado, pero aqui sale de
medir, no de una suposicion, asi que soporta cualquier otro par de fps.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult
from ..probe import probe_file, require_binary
from ..sources import ClassifiedSources
from ..toolinfo import ffmpeg_version

# Tolerancia razonable para audio de largometraje: unos pocos frames a 24 fps.
DURATION_TOLERANCE_SECONDS = 0.5


class RetimeError(RuntimeError):
    pass


def compute_atempo_ratio(source_fps: Fraction, target_fps: Fraction) -> Fraction:
    if source_fps <= 0:
        raise RetimeError(f"fps de origen invalido: {source_fps}")
    return target_fps / source_fps


def _atempo_arg(ratio: Fraction) -> str:
    """Mismo formato de 5 decimales que las notas SH1984 (p.ej. 0.95904)."""
    return f"{float(ratio):.5f}"


def _run_ffmpeg_retime(input_path: Path, output_path: Path, ratio: Fraction, ffmpeg_path: str) -> None:
    ffmpeg = require_binary("ffmpeg", ffmpeg_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-ar", "48000",
        "-filter:a", f"atempo={_atempo_arg(ratio)}",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RetimeError(f"ffmpeg fallo retimeando {input_path.name}:\n{result.stderr.strip()}")


def retime_step(
    sources: ClassifiedSources,
    layout: EpisodeLayout,
    manifest: Manifest,
    ffmpeg_path: str = "ffmpeg",
    force: bool = False,
) -> tuple[Path, StepResult | None]:
    """Devuelve (ruta_a_usar_como_ES_para_separar, StepResult|None).

    Si el DVD ya viene al fps destino, no hay nada que retimear: se
    devuelve directamente `es_source.flac` y el segundo elemento es None.
    """
    if sources.dvd_video is None:
        raise RetimeError(f"{sources.dvd_container.name}: no se pudo leer el frame rate de su video")

    source_fps = sources.dvd_video.frame_rate
    target_fps = sources.target_fps

    if source_fps == target_fps:
        return layout.es_source, None

    ratio = compute_atempo_ratio(source_fps, target_fps)
    output = layout.es_retimed(target_fps)
    params = {
        "source_fps": str(source_fps),
        "target_fps": str(target_fps),
        "ratio": str(ratio),
        "atempo_arg": _atempo_arg(ratio),
    }
    tool_versions = {"ffmpeg": ffmpeg_version(ffmpeg_path)}

    def _run_and_verify() -> None:
        _run_ffmpeg_retime(layout.es_source, output, ratio, ffmpeg_path)
        in_duration = probe_file(layout.es_source, ffmpeg_path.replace("ffmpeg", "ffprobe")).duration
        out_duration = probe_file(output, ffmpeg_path.replace("ffmpeg", "ffprobe")).duration
        expected = in_duration / float(ratio)
        if abs(out_duration - expected) > DURATION_TOLERANCE_SECONDS:
            raise RetimeError(
                f"Duracion de {output.name} fuera de tolerancia: "
                f"esperada ~{expected:.3f}s, obtenida {out_duration:.3f}s "
                f"(entrada {in_duration:.3f}s, ratio {ratio})"
            )

    result = manifest.run_step(
        step_name="retime:es",
        input_paths=[layout.es_source],
        params=params,
        output_paths=[output],
        tool_versions=tool_versions,
        fn=_run_and_verify,
        force=force,
    )
    return output, result
