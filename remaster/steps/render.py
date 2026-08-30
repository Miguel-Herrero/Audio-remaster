"""Paso 8: render headless con REAPER (`-renderproject`). Bloquea hasta
terminar y sale solo — sin reapy, sin sesion interactiva, sin el
`sleep(2)` + restauracion de mute/solo que tenia el script viejo (que ni
siquiera esperaba a que el render terminase de verdad).

Lo que se renderiza es `<ep>_render.RPP`, derivado aqui mismo a partir
de `<ep>.RPP` justo antes de renderizar (ver reaper/rpp_render.py). Antes
lo escribia el paso `project` a la vez que el editable, y se quedaba
congelado en la version recien generada: si habias editado el proyecto a
mano — que es el flujo previsto — el render tiraba todo ese trabajo.
Derivarlo aqui hace ademas que el paso dependa del fichero editado, asi
que tocarlo invalida el render, como debe ser.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult
from ..probe import probe_file
from ..profile import Profile
from ..reaper.rpp_render import RenderCopyPlan, write_render_project
from .project import AUDIBLE_IN_RENDER, MUTED_IN_RENDER

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


def render_copy_plan(layout: EpisodeLayout, profile: Profile) -> RenderCopyPlan:
    """Que pistas silenciar y cuales forzar audibles, traducido de claves
    de pista a los nombres que pone el perfil de serie.
    """
    by_key = {
        "VIDEO": profile.tracks.video, "EN_VOX": profile.tracks.en_vox,
        "EN_ME": profile.tracks.en_me, "ES_VOX": profile.tracks.es_vox,
        "ES_ME": profile.tracks.es_me,
    }
    return RenderCopyPlan(
        output_file=layout.es_reconstructed,
        mute=frozenset(by_key[k] for k in MUTED_IN_RENDER),
        unmute=frozenset(by_key[k] for k in AUDIBLE_IN_RENDER),
    )


def render_step(
    layout: EpisodeLayout,
    expected_duration_s: float,
    manifest: Manifest,
    profile: Profile,
    reaper_binary: str = REAPER_BINARY,
    force: bool = False,
) -> StepResult:
    if not layout.rpp_edit.exists():
        raise RenderError(f"No hay proyecto que renderizar en {layout.rpp_edit}. Ejecuta antes el paso 'project'.")
    plan = render_copy_plan(layout, profile)

    def fn() -> None:
        write_render_project(layout.rpp_edit, layout.rpp_render, plan)
        render_project(layout.rpp_render, reaper_binary)
        _verify_output(layout.es_reconstructed, expected_duration_s)

    return manifest.run_step(
        step_name="render",
        input_paths=[layout.rpp_edit],
        params={
            "reaper_binary": reaper_binary,
            "expected_duration_s": round(expected_duration_s, 3),
            "muted": sorted(plan.mute),
            "audible": sorted(plan.unmute),
        },
        output_paths=[layout.es_reconstructed],
        tool_versions={},
        fn=fn,
        force=force,
    )
