"""Deriva `<ep>_render.RPP` DESDE `<ep>.RPP`, en vez de generarlo aparte.

Antes los dos ficheros se escribian a la vez desde los datos del
pipeline: uno con todo audible para editar a mano, y otro con EN VOX y
ES M&E muteados para `REAPER -renderproject`. El problema es evidente en
cuanto se usa el flujo tal y como esta pensado: el usuario abre el
primero, afina el sync partiendo la pista en cientos de items, rellena
huecos, retoca la envolvente... y el segundo sigue siendo la version
recien generada. Medido en s03e01: 352 items en el editado, 25 en el de
render, con dia y medio de trabajo entre uno y otro. Renderizar por el
pipeline habria tirado todo ese trabajo sin avisar.

La solucion NO es renderizar directamente `<ep>.RPP`. Ese fichero lleva
los ajustes del dialogo de Render que haya dejado el usuario en su ultima
prueba — en s03e01, `RENDER_FILE` apuntaba a `02_finals/hudson_risa.flac`,
un render suelto para escuchar una risa. Renderizarlo tal cual habria
escrito ahi en vez de en `es_reconstructed.flac`.

Asi que el fichero de render se conserva, pero pasa a ser una COPIA del
editado con dos cosas forzadas: el destino y el formato de render, y el
mute de las pistas que no deben sonar. Todo lo demas se copia linea a
linea, byte a byte: items, envolventes, FX, y cualquier pista nueva que
el usuario haya añadido — que asi entra en el render, que es lo que uno
espera al añadirla.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .rpp import RENDER_CFG_FLAC16

# Ajustes de render que se imponen, copiados de _HEADER_TEMPLATE en
# rpp.py — el mismo FLAC 16-bit que genera el pipeline. Cualquier otro
# RENDER_* que el usuario haya tocado se respeta.
FORCED_RENDER_LINES: dict[str, str] = {
    "RENDER_FMT": "RENDER_FMT 0 2 0",
    "RENDER_1X": "RENDER_1X 0",
    "RENDER_RANGE": "RENDER_RANGE 1 0 0 0 1000",
    "RENDER_RESAMPLE": "RENDER_RESAMPLE 3 0 1",
    "RENDER_ADDTOPROJ": "RENDER_ADDTOPROJ 0",
    "RENDER_STEMS": "RENDER_STEMS 0",
    "RENDER_DITHER": "RENDER_DITHER 0",
    "RENDER_TRIM": "RENDER_TRIM 0.000001 0.000001 0 0",
}


class RenderProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderCopyPlan:
    output_file: Path
    mute: frozenset[str]  # nombres de pista que NO deben sonar
    unmute: frozenset[str]  # nombres de pista que SI deben sonar, pase lo que pase
    render_cfg: str = RENDER_CFG_FLAC16


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _tag(stripped: str) -> str:
    return stripped[1:].split(" ", 1)[0] if len(stripped) > 1 else ""


def _track_name(stripped: str) -> str:
    """`NAME "ES VOX"` -> `ES VOX`. REAPER entrecomilla solo si hace falta."""
    value = stripped[len("NAME"):].strip()
    if value[:1] in ("\"", "'", "`") and value[-1:] == value[:1]:
        return value[1:-1]
    return value


def derive_render_project(text: str, plan: RenderCopyPlan) -> str:
    """Copia `text` forzando destino de render y mutes. Todo lo demas se
    conserva tal cual.
    """
    lines = text.splitlines()
    out: list[str] = []
    stack: list[str] = []
    track_name: str | None = None
    skipping_render_cfg = False
    seen_render_file = False
    seen_tracks: set[str] = set()

    for raw in lines:
        stripped = raw.strip()

        if skipping_render_cfg:
            # El cuerpo viejo del bloque se descarta; el cierre se conserva.
            if stripped == ">":
                skipping_render_cfg = False
                stack.pop()
                out.append(raw)
            continue

        if stripped.startswith("<"):
            tag = _tag(stripped)
            stack.append(tag)
            out.append(raw)
            if tag == "TRACK":
                track_name = None
            elif tag == "RENDER_CFG" and stack[:1] == ["REAPER_PROJECT"] and len(stack) == 2:
                out.append(f"{_indent_of(raw)}  {plan.render_cfg}")
                skipping_render_cfg = True
            continue

        if stripped == ">":
            if stack:
                stack.pop()
            out.append(raw)
            continue

        at_project = len(stack) == 1 and stack[0] == "REAPER_PROJECT"
        at_track = len(stack) == 2 and stack[1] == "TRACK"

        if at_project:
            key = stripped.split(" ", 1)[0]
            if key == "RENDER_FILE":
                out.append(f'{_indent_of(raw)}RENDER_FILE "{plan.output_file}"')
                seen_render_file = True
                continue
            if key in FORCED_RENDER_LINES:
                out.append(f"{_indent_of(raw)}{FORCED_RENDER_LINES[key]}")
                continue

        if at_track:
            if stripped.startswith("NAME"):
                track_name = _track_name(stripped)
                seen_tracks.add(track_name)
            elif stripped.startswith("MUTESOLO") and track_name is not None:
                # Solo se tocan las pistas nombradas. Una pista nueva que
                # haya añadido el usuario se queda como la dejo: si la
                # dejo sonando, suena en el render.
                if track_name in plan.mute:
                    out.append(f"{_indent_of(raw)}MUTESOLO 1 0 0")
                    continue
                if track_name in plan.unmute:
                    out.append(f"{_indent_of(raw)}MUTESOLO 0 0 0")
                    continue

        out.append(raw)

    if not seen_render_file:
        raise RenderProjectError(
            "el proyecto no tiene linea RENDER_FILE: no se puede fijar el destino del render"
        )
    missing = (plan.mute | plan.unmute) - seen_tracks
    if missing:
        raise RenderProjectError(
            f"faltan pistas en el proyecto: {', '.join(sorted(missing))}. "
            "¿Las has renombrado o borrado? Los nombres salen del perfil de serie."
        )
    return "\n".join(out) + "\n"


def write_render_project(edited_path: Path, render_path: Path, plan: RenderCopyPlan) -> None:
    render_path.parent.mkdir(parents=True, exist_ok=True)
    derived = derive_render_project(edited_path.read_text(encoding="utf-8", errors="replace"), plan)
    render_path.write_text(derived, encoding="utf-8", newline="\n")
