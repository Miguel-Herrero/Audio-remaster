"""Genera `<ep>/NOTES.md` desde el manifiesto. No puede desviarse de lo
que realmente se ejecuto porque no lo escribe nadie a mano — al contrario
que las notas SH1984/*.md, cuyo paso 1 en v1.0.1.md documentaba mal de
donde salia el audio ingles (ver plan v2, seccion "Documentacion").
"""

from __future__ import annotations

from pathlib import Path

from .layout import EpisodeLayout
from .manifest import Manifest

_STEP_ORDER = [
    "extract:es", "extract:en", "extract:ja", "retime:es",
    "separate:es", "separate:en", "align", "loudness",
    "project", "render", "mux", "remux4k",
]
_STEP_TITLES = {
    "extract:es": "Extraccion — audio castellano",
    "extract:en": "Extraccion — audio ingles",
    "extract:ja": "Extraccion — audio japones",
    "retime:es": "Correccion de frame rate",
    "separate:es": "Separacion de stems — castellano",
    "separate:en": "Separacion de stems — ingles",
    "align": "Sincronizacion automatica",
    "loudness": "Loudness (LUFS)",
    "project": "Proyecto REAPER",
    "render": "Render",
    "mux": "Mux final",
    "remux4k": "Remux 4K",
}


def _step_sort_key(step_name: str) -> int:
    return _STEP_ORDER.index(step_name) if step_name in _STEP_ORDER else len(_STEP_ORDER)


def generate_notes(layout: EpisodeLayout, manifest: Manifest) -> str:
    lines = [
        f"# Notas de reconstruccion — {layout.code}",
        "",
        "Generado automaticamente por `remaster notes` desde `.remaster/manifest.json`. "
        "No editar a mano: se sobreescribe en cada ejecucion.",
        "",
    ]

    for step_name in sorted(manifest.data, key=_step_sort_key):
        record = manifest.data[step_name]
        lines.append(f"## {_STEP_TITLES.get(step_name, step_name)}")
        lines.append("")
        lines.append(f"- Ejecutado: {record.get('recorded_at', '?')} ({record.get('duration_seconds', '?')}s)")
        for tool, version in record.get("tool_versions", {}).items():
            lines.append(f"- {tool}: `{version}`")
        params = record.get("params", {})
        if params:
            lines.append("- Parametros:")
            for k, v in params.items():
                lines.append(f"  - `{k}` = `{v}`")
        inputs = record.get("inputs", {})
        if inputs:
            lines.append("- Entradas:")
            for path in inputs:
                lines.append(f"  - `{Path(path).name}`")
        lines.append("")

    return "\n".join(lines)


def write_notes(layout: EpisodeLayout, manifest: Manifest) -> Path:
    text = generate_notes(layout, manifest)
    layout.notes_path.parent.mkdir(parents=True, exist_ok=True)
    layout.notes_path.write_text(text)
    return layout.notes_path
