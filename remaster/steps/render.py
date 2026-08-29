"""Paso 8: render headless con REAPER (`-renderproject`). Bloquea hasta
terminar y sale solo — sin reapy, sin sesion interactiva, sin el
`sleep(2)` + restauracion de mute/solo que tenia el script viejo (que ni
siquiera esperaba a que el render terminase de verdad).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult
from ..probe import probe_file

REAPER_BINARY = "/Applications/REAPER.app/Contents/MacOS/REAPER"
DURATION_TOLERANCE_SECONDS = 1.0


class RenderError(RuntimeError):
    pass


def render_project(rpp_path: Path, reaper_binary: str = REAPER_BINARY, timeout_s: float = 3600.0) -> None:
    if not Path(reaper_binary).exists():
        raise RenderError(f"No se encuentra REAPER en {reaper_binary}. Ejecuta 'remaster doctor'.")
    result = subprocess.run(
        [reaper_binary, "-renderproject", str(rpp_path)],
        capture_output=True, text=True, timeout=timeout_s,
    )
    if result.returncode != 0:
        raise RenderError(
            f"REAPER devolvio codigo {result.returncode} renderizando {rpp_path.name}:\n"
            f"{result.stderr.strip()}\n{result.stdout.strip()}"
        )


def _verify_output(output_path: Path, expected_duration_s: float) -> None:
    if not output_path.exists():
        raise RenderError(f"REAPER termino sin error pero no genero {output_path}")
    probe = probe_file(output_path)
    if abs(probe.duration - expected_duration_s) > DURATION_TOLERANCE_SECONDS:
        raise RenderError(
            f"{output_path.name}: duracion {probe.duration:.3f}s fuera de tolerancia "
            f"(esperada ~{expected_duration_s:.3f}s, referencia EN VOX)"
        )
    audio = probe.audio[0] if probe.audio else None
    if not audio or audio.sample_rate != 48000:
        raise RenderError(f"{output_path.name}: sample rate inesperado ({audio.sample_rate if audio else 'ninguno'})")


def render_step(
    layout: EpisodeLayout,
    expected_duration_s: float,
    manifest: Manifest,
    reaper_binary: str = REAPER_BINARY,
    force: bool = False,
) -> StepResult:
    def fn() -> None:
        render_project(layout.rpp_render, reaper_binary)
        _verify_output(layout.es_reconstructed, expected_duration_s)

    return manifest.run_step(
        step_name="render",
        input_paths=[layout.rpp_render],
        params={"reaper_binary": reaper_binary, "expected_duration_s": round(expected_duration_s, 3)},
        output_paths=[layout.es_reconstructed],
        tool_versions={},
        fn=fn,
        force=force,
    )
