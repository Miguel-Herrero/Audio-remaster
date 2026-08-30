"""Lector de proyectos REAPER (.RPP). El complemento de `rpp.py`, que solo
escribe.

Existe porque en cuanto el usuario abre el proyecto y edita a mano, el
`.RPP` deja de ser lo que el pipeline genero: los items se parten y se
mueven uno a uno para afinar el sync, y a veces se rellenan huecos con
audio de OTRA fuente (un trozo de `en_vocals.flac` que suena mejor, una
pista mejor aislada tipo `speech.wav`). Ese fichero editado es la unica
descripcion fiel de lo que suena, asi que cualquier verificacion
posterior tiene que leerlo a el, no a `loudness.json`.

El formato es un arbol de bloques `<TAG ...` / `>` con lineas de tokens
sueltas dentro. Se parsea como tal en vez de con expresiones regulares
por linea: un `>` a secas cierra el bloque abierto, y sin llevar la
cuenta de la anidacion no se distingue el `>` de un `<SOURCE` del de su
`<ITEM`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# --- arbol generico ---


@dataclass
class Node:
    tag: str
    attrs: tuple[str, ...] = ()
    lines: list[tuple[str, ...]] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def child(self, tag: str) -> "Node | None":
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def children_named(self, tag: str) -> list["Node"]:
        return [c for c in self.children if c.tag == tag]

    def line(self, key: str) -> tuple[str, ...] | None:
        for tokens in self.lines:
            if tokens and tokens[0] == key:
                return tokens[1:]
        return None

    def float_at(self, key: str, index: int = 0, default: float | None = None) -> float | None:
        tokens = self.line(key)
        if tokens is None or index >= len(tokens):
            return default
        try:
            return float(tokens[index])
        except ValueError:
            return default


def tokenize(line: str) -> tuple[str, ...]:
    """Trocea una linea de RPP respetando los tres tipos de comilla que usa
    REAPER (`"`, `'`, backtick — elige la que no aparece en el valor).
    """
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in line.strip():
        if quote:
            if ch == quote:
                tokens.append("".join(current))
                current = []
                quote = None
            else:
                current.append(ch)
        elif ch in "\"'`" and not current:
            quote = ch
        elif ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def parse_rpp(text: str) -> Node:
    root = Node(tag="ROOT")
    stack = [root]
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped == ">":
            if len(stack) > 1:
                stack.pop()
            continue
        tokens = tokenize(stripped)
        if not tokens:
            continue
        if tokens[0].startswith("<"):
            node = Node(tag=tokens[0][1:], attrs=tokens[1:])
            stack[-1].children.append(node)
            stack.append(node)
        else:
            stack[-1].lines.append(tokens)
    return root


def load_rpp(path: Path) -> Node:
    return parse_rpp(path.read_text(errors="replace"))


# --- vista de dominio: pistas, items, envolvente ---


@dataclass(frozen=True)
class ReadItem:
    position: float
    length: float
    soffs: float
    playrate: float
    volume: float  # ganancia lineal del propio item (VOLPAN campo 1)
    muted: bool
    source_file: Path | None
    name: str

    @property
    def end(self) -> float:
        return self.position + self.length

    def source_time_at(self, timeline_t: float) -> float:
        """Instante del fichero fuente que suena en `timeline_t`."""
        return self.soffs + (timeline_t - self.position) * self.playrate


@dataclass(frozen=True)
class ReadTrack:
    name: str
    items: tuple[ReadItem, ...]
    vol_points: tuple[tuple[float, float], ...]  # (tiempo, ganancia lineal)
    js_volume_db: float | None  # ganancia del JSFX utility/volume, si esta
    fx_names: tuple[str, ...]

    def item_at(self, t: float) -> ReadItem | None:
        for item in self.items:
            if item.position <= t < item.end and not item.muted:
                return item
        return None

    def sources(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.items:
            if item.source_file is None:
                continue
            name = item.source_file.name
            if name not in seen:
                seen.append(name)
        return tuple(seen)


def _read_item(node: Node) -> ReadItem:
    source = node.child("SOURCE")
    source_file = None
    if source is not None:
        file_tokens = source.line("FILE")
        if file_tokens:
            source_file = Path(file_tokens[0])
    name_tokens = node.line("NAME")
    mute_tokens = node.line("MUTE")
    return ReadItem(
        position=node.float_at("POSITION", default=0.0) or 0.0,
        length=node.float_at("LENGTH", default=0.0) or 0.0,
        soffs=node.float_at("SOFFS", default=0.0) or 0.0,
        playrate=node.float_at("PLAYRATE", default=1.0) or 1.0,
        volume=node.float_at("VOLPAN", default=1.0) or 1.0,
        muted=bool(mute_tokens and mute_tokens[0] not in ("0", "0.0")),
        source_file=source_file,
        name=name_tokens[0] if name_tokens else (source_file.name if source_file else ""),
    )


def _read_js_volume_db(track: Node) -> float | None:
    """El JSFX `utility/volume` guarda sus sliders en la linea siguiente al
    bloque `<JS utility/volume`: el primer valor es la ganancia en dB
    (ver rpp.py:js_volume_fx_lines).
    """
    fxchain = track.child("FXCHAIN")
    if fxchain is None:
        return None
    for node in fxchain.children_named("JS"):
        if node.attrs and node.attrs[0] == "utility/volume" and node.lines:
            try:
                return float(node.lines[0][0])
            except (IndexError, ValueError):
                return None
    return None


def _read_fx_names(track: Node) -> tuple[str, ...]:
    fxchain = track.child("FXCHAIN")
    if fxchain is None:
        return ()
    names = []
    for node in fxchain.children:
        if node.tag == "JS" and node.attrs:
            names.append(f"JS: {node.attrs[0]}")
        elif node.tag == "VST" and node.attrs:
            names.append(node.attrs[0])
    return tuple(names)


def read_tracks(root: Node) -> tuple[ReadTrack, ...]:
    project = root.child("REAPER_PROJECT") or root
    tracks = []
    for node in project.children_named("TRACK"):
        name_tokens = node.line("NAME")
        env = node.child("VOLENV2")
        points: tuple[tuple[float, float], ...] = ()
        if env is not None:
            points = tuple(
                (float(t[0]), float(t[1]))
                for t in (tokens[1:] for tokens in env.lines if tokens[0] == "PT")
                if len(t) >= 2
            )
        tracks.append(ReadTrack(
            name=name_tokens[0] if name_tokens else "",
            items=tuple(_read_item(i) for i in node.children_named("ITEM")),
            vol_points=points,
            js_volume_db=_read_js_volume_db(node),
            fx_names=_read_fx_names(node),
        ))
    return tuple(tracks)


def find_track(tracks: tuple[ReadTrack, ...], name: str) -> ReadTrack | None:
    for track in tracks:
        if track.name == name:
            return track
    return None


def envelope_gain_db(points: tuple[tuple[float, float], ...], t: float) -> float:
    """Ganancia de la envolvente en `t`, en dB. Interpolacion lineal en el
    dominio de ganancia (shape 0 de REAPER). Sin envolvente -> 0 dB.
    """
    if not points:
        return 0.0
    import math

    if t <= points[0][0]:
        gain = points[0][1]
    elif t >= points[-1][0]:
        gain = points[-1][1]
    else:
        gain = points[0][1]
        for (t0, g0), (t1, g1) in zip(points, points[1:]):
            if t0 <= t <= t1:
                gain = g0 if t1 == t0 else g0 + (g1 - g0) * (t - t0) / (t1 - t0)
                break
    return 20 * math.log10(gain) if gain > 0 else -120.0


def _envelope_samples(points: tuple[tuple[float, float], ...], start: float, end: float, samples: int) -> list[float]:
    """Se muestrea en vez de integrar analiticamente: los tramos son
    mesetas con rampas de 0.15s (ver steps/loudness.py:RAMP_SECONDS), asi
    que 32 muestras sobran.
    """
    if end <= start:
        return [envelope_gain_db(points, start)]
    step = (end - start) / samples
    return [envelope_gain_db(points, start + step * (i + 0.5)) for i in range(samples)]


def envelope_mean_db(points: tuple[tuple[float, float], ...], start: float, end: float, samples: int = 32) -> float:
    """dB medio de la envolvente sobre [start, end) — lo que le hace al
    NIVEL del tramo."""
    values = _envelope_samples(points, start, end, samples)
    return sum(values) / len(values)


def envelope_max_db(points: tuple[tuple[float, float], ...], start: float, end: float, samples: int = 32) -> float:
    """dB maximo de la envolvente sobre [start, end) — lo que le hace al
    PICO del tramo, que es lo que importa para el recorte."""
    return max(_envelope_samples(points, start, end, samples))
