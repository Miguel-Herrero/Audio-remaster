"""Lector del `render_stats.html` que REAPER escribe tras un render (o un
dry run) con "Loudness/peak analysis" activado.

Es la unica medida POST-FX que tenemos sin renderizar de verdad: incluye
ReaEQ y ReaFir, que el analisis sobre las fuentes no puede ver. De ahi
que `steps/verify.py` lo acepte como entrada opcional para el pico y el
"impuesto" de la cadena de FX.

Formato: una tabla HTML con una fila por fichero/pista y, debajo, un
bloque `const data_N={...}` por pista con la serie temporal. Los valores
en dB van como texto dentro de la serie (`[0.1234,'-23.43']`), asi que se
leen de ahi y no del primer numero, que es la coordenada normalizada
para dibujar el canvas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ROW_RE = re.compile(r"<tr>\s*(?:<td>.*?</td>\s*)+</tr>", re.S)
_CELL_RE = re.compile(r"<td>(.*?)</td>", re.S)
_BLOCK_RE = re.compile(r"const data_\d+=\{(.*?)\n\};", re.S)
_NAME_RE = re.compile(r"name:'([^']*)'")
_SERIES_RE = re.compile(r"series:\[([^\]]*)\]")
# La coma final es opcional a proposito: la ultima fila de `vals` no la
# lleva, y exigirla se comia justo esa fila — el final del episodio.
_ROW_VALS_RE = re.compile(r"\[\[([\d.eE+-]+),'([^']*)'\](.*?)\],?[ \t]*\n")
_DB_RE = re.compile(r"\[[-\d.eE+]+,'([^']*)'\]")


class RenderStatsError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackStats:
    name: str
    length_s: float
    peak_dbfs: float
    clips: int
    max_lufs_m: float
    max_lufs_s: float
    lufs_i: float
    lra: float
    times: tuple[float, ...]
    lufs_m: tuple[float, ...]
    lufs_s: tuple[float, ...]


def _db(value: str) -> float:
    value = value.strip()
    if value in ("-inf", "", "-"):
        return float("-inf")
    try:
        return float(value)
    except ValueError:
        return float("nan")


def parse_timecode(value: str) -> float:
    """'53:02.805' -> 3182.805. Acepta H:M:S y S sueltos."""
    parts = value.strip().split(":")
    try:
        seconds = float(parts[-1])
    except ValueError as exc:
        raise RenderStatsError(f"timecode ilegible: {value!r}") from exc
    for i, part in enumerate(reversed(parts[:-1]), start=1):
        seconds += float(part) * (60 ** i)
    return seconds


def _parse_series(html: str) -> dict[str, tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]]:
    out = {}
    for block in _BLOCK_RE.finditer(html):
        body = block.group(1)
        name_match = _NAME_RE.search(body)
        series_match = _SERIES_RE.search(body)
        if not name_match or not series_match:
            continue
        labels = [s.strip().strip("'\"") for s in series_match.group(1).split(",")]
        try:
            i_m = labels.index("LUFS-M")
            i_s = labels.index("LUFS-S")
        except ValueError:
            continue
        times: list[float] = []
        lufs_m: list[float] = []
        lufs_s: list[float] = []
        for _, timecode, rest in _ROW_VALS_RE.findall(body + "\n"):
            values = _DB_RE.findall(rest)
            if len(values) <= max(i_m, i_s):
                continue
            times.append(parse_timecode(timecode))
            lufs_m.append(_db(values[i_m]))
            lufs_s.append(_db(values[i_s]))
        out[name_match.group(1)] = (tuple(times), tuple(lufs_m), tuple(lufs_s))
    return out


def parse_render_stats(path: Path) -> tuple[TrackStats, ...]:
    html = path.read_text(errors="replace")
    series = _parse_series(html)

    tracks: list[TrackStats] = []
    for row in _ROW_RE.finditer(html):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL_RE.findall(row.group(0))]
        if len(cells) < 8:
            continue
        name = cells[0]
        times, lufs_m, lufs_s = series.get(name, ((), (), ()))
        try:
            tracks.append(TrackStats(
                name=name,
                length_s=parse_timecode(cells[1]),
                peak_dbfs=_db(cells[2]),
                clips=int(cells[3] or 0),
                max_lufs_m=_db(cells[4]),
                max_lufs_s=_db(cells[5]),
                lufs_i=_db(cells[6]),
                lra=_db(cells[7]),
                times=times, lufs_m=lufs_m, lufs_s=lufs_s,
            ))
        except (ValueError, RenderStatsError):
            continue

    if not tracks:
        raise RenderStatsError(f"{path.name}: no se reconocio ninguna fila de pista")
    return tuple(tracks)


def find_stats(tracks: tuple[TrackStats, ...], name: str) -> TrackStats | None:
    for track in tracks:
        if track.name == name:
            return track
    return None


def newest_render_stats(search_dirs: list[Path]) -> Path | None:
    """El `render_stats.html` mas reciente. REAPER lo deja en el temporal
    del sistema, no junto al proyecto, asi que la UI necesita buscarlo.
    """
    candidates: list[Path] = []
    for directory in search_dirs:
        if not directory or not directory.is_dir():
            continue
        candidates.extend(directory.glob("render_stats*.html"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
