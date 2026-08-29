"""Generador de proyectos REAPER (.RPP) desde dataclasses — sin reapy, sin
sesion interactiva. El formato de cada bloque (TRACK, ITEM, VOLENV2,
FXCHAIN, cabecera del proyecto) esta copiado literalmente de un proyecto
real abierto y guardado por REAPER 7.71
(`/Users/miguelherrerobaena/Movies/s02e06/s02e06/s02e06.RPP`), no
adivinado: donde el formato importaba (el blob binario del preset ReaEQ
"Holmes_1.0.1", el `RENDER_CFG` de FLAC 16-bit, la semantica del JSFX
"utility/volume") se verifico contra ese fichero y contra el propio
codigo fuente del JSFX instalado en REAPER.

GUIDs deterministas (uuid5 sobre una semilla estable) en vez de aleatorios:
regenerar el mismo proyecto con las mismas entradas produce bytes
identicos — necesario para el test de snapshot y para que `manifest.py`
detecte cambios reales.

Nota sobre ReaFir: se inserta en ES VOX con el estado por defecto (sin
perfil de ruido capturado) y BYPASS 1, listo para que el usuario capture
su propio perfil a mano en REAPER. El blob de estado esta capturado de
un proyecto real (`s03e01.RPP`), no fabricado — un blob inventado se
comprobo empiricamente que cuelga REAPER en render headless.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

REAPER_VERSION_TAG = "7.71/macOS-arm64"

# --- capturado literalmente de s02e06.RPP ---

# `calf` + 16-bit + compression level 8 (RENDER_FMT 0 2 0 = FLAC).
RENDER_CFG_FLAC16 = "Y2FsZhAAAAAIAAAA"

REAEQ_HOLMES_STATE_LINES = (
    "cWVlcu5e7f4CAAAAAQAAAAAAAAACAAAAAAAAAAIAAAABAAAAAAAAAAIAAAAAAAAArAAAAAEAAAAAABAA",
    "IQAAAAQAAAAEAAAAAQAAAAAAAAAAAFRAaLjUSnrujTUAAAAAAAAAQAEIAAAAAQAAAAAAAAAAgGZAiqvo6n2n5j8AAAAAAADwPwEIAAAAAQAAAAAAAAAAAHlANMTNCyNr",
    "6T8zMzMzMzPzPwEBAAAAAQAAAAAAAAAAiLNAiqvo6n2n5j8AAAAAAAAAQAEBAAAAAQAAAAAAAAAAAPA/AAAAAEACAABsAQAAAgAAAA==",
    "AEhvbG1lc18xLjAuMQAQAAAA",
)
REAEQ_PRESET_NAME = "Holmes_1.0.1"

# Estado por defecto de ReaFir (sin perfil de ruido capturado), tal cual lo
# guarda REAPER al insertar el FX sin tocarlo. Capturado de
# `/Users/miguelherrerobaena/Movies/s03e01/s03e01/s03e01.RPP` — no
# fabricado (ver nota de modulo). Se inserta con BYPASS 1: usable a mano
# sin que afecte al render hasta que el usuario le capture un perfil.
REAFIR_DEFAULT_STATE_LINES = (
    "cmZlcu5e7f4CAAAAAQAAAAAAAAACAAAAAAAAAAIAAAABAAAAAAAAAAIAAAAAAAAAWAAAAAEAAAAAABAA",
    "nBIAAAAAtMIAAMBBABAAAAAAgD8AAAAAAgAAAAIAAADXOb9DAAAAAKUrSEUAAAAABAAAAAAAAACtVQM8AACAP7OiBDgAAAA+AAAAAAAAAAACAAACAACAPw==",
    "AAAQAAAA",
)

# JSFX "utility/volume" (desc: "Volume Adjustment", ver
# ~/Library/Application Support/REAPER/Effects/utility/volume):
# slider1 = Adjustment (dB), slider2 = Max Volume (dB, 0 = sin recorte
# extra). El resto son 62 slots sin usar, serializados como "-".
_JS_VOLUME_UNUSED_SLOTS = " ".join(["-"] * 62)


def deterministic_guid(seed: str) -> str:
    """{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX} estable a partir de una
    semilla de texto. Nunca aleatorio: mismo input, mismo proyecto.
    """
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed)).upper() + "}"


def _fmt(n: float) -> str:
    if float(n).is_integer():
        return str(int(n))
    return repr(round(float(n), 8))


def _quoted(s: str) -> str:
    return f'"{s}"' if any(c.isspace() for c in s) or s == "" else s


@dataclass(frozen=True)
class VolPoint:
    """Un punto de la envolvente de volumen (ganancia lineal, no dB)."""
    time: float
    gain: float
    shape: int = 0


@dataclass(frozen=True)
class Item:
    position: float
    length: float
    name: str
    source_file: Path
    soffs: float = 0.0
    source_kind: str = "FLAC"  # "FLAC" | "VIDEO"
    fade_in: float = 0.01
    fade_out: float = 0.01
    playrate: float = 1.0  # 1.0 = sin time-stretch; el pipeline no lo usa (drift se corrige con cortes, ver steps/project.py), disponible para ajuste manual


@dataclass(frozen=True)
class Track:
    key: str  # identificador estable para GUIDs, p.ej. "ES_VOX"
    name: str
    items: tuple[Item, ...] = ()
    mainsend: bool = True
    muted: bool = False
    vol_points: tuple[VolPoint, ...] = ()
    fx_lines: tuple[str, ...] = ()  # ya formateadas, ver js_volume_fx / reaeq_holmes_fx


@dataclass(frozen=True)
class RenderSettings:
    output_file: Path
    render_cfg: str = RENDER_CFG_FLAC16


@dataclass(frozen=True)
class Project:
    episode_code: str
    tracks: tuple[Track, ...]
    render: RenderSettings
    project_id: int = 0  # solo cosmetico; ver build_project()


def js_volume_fx_lines(gain_db: float, episode_code: str, track_key: str) -> tuple[str, ...]:
    fxid = deterministic_guid(f"{episode_code}:fx:{track_key}:js_volume")
    state = f"{_fmt(round(gain_db, 4))} 0 {_JS_VOLUME_UNUSED_SLOTS}"
    return (
        "      BYPASS 0 0 0",
        '      <JS utility/volume ""',
        f"        {state}",
        "      >",
        "      FLOATPOS 0 0 0 0",
        f"      FXID {fxid}",
        "      WAK 0 0",
    )


def reaeq_holmes_fx_lines(episode_code: str, track_key: str) -> tuple[str, ...]:
    fxid = deterministic_guid(f"{episode_code}:fx:{track_key}:reaeq")
    lines = [
        "      BYPASS 0 0 0",
        '      <VST "VST: ReaEQ (Cockos)" reaeq.vst.dylib 0 "" '
        '1919247729<56535472656571726561657100000000> ""',
    ]
    lines += [f"        {l}" for l in REAEQ_HOLMES_STATE_LINES]
    lines += [
        "      >",
        f"      PRESETNAME {REAEQ_PRESET_NAME}",
        "      FLOATPOS 0 0 0 0",
        f"      FXID {fxid}",
        "      WAK 0 0",
    ]
    return tuple(lines)


def reafir_default_fx_lines(episode_code: str, track_key: str) -> tuple[str, ...]:
    fxid = deterministic_guid(f"{episode_code}:fx:{track_key}:reafir")
    lines = [
        "      BYPASS 1 0 0",
        '      <VST "VST: ReaFir (FFT EQ+Dynamics Processor) (Cockos)" reafir.vst.dylib 0 "" '
        '1919247986<56535472656672726561666972666674> ""',
    ]
    lines += [f"        {l}" for l in REAFIR_DEFAULT_STATE_LINES]
    lines += [
        "      >",
        "      FLOATPOS 0 0 0 0",
        f"      FXID {fxid}",
        "      WAK 0 0",
    ]
    return tuple(lines)


def _render_item(item: Item, episode_code: str, track_key: str, index: int) -> str:
    iguid = deterministic_guid(f"{episode_code}:item:{track_key}:{index}:i")
    guid = deterministic_guid(f"{episode_code}:item:{track_key}:{index}:g")
    return "\n".join((
        "    <ITEM",
        f"      POSITION {_fmt(item.position)}",
        "      SNAPOFFS 0",
        f"      LENGTH {_fmt(item.length)}",
        "      LOOP 1",
        "      ALLTAKES 0",
        f"      FADEIN 1 {item.fade_in} 0 1 0 0 0",
        f"      FADEOUT 1 {item.fade_out} 0 1 0 0 0",
        "      MUTE 0 0",
        "      SEL 0",
        f"      IGUID {iguid}",
        "      IID 1",
        f"      NAME {_quoted(item.name)}",
        "      VOLPAN 1 0 1 -1",
        f"      SOFFS {_fmt(item.soffs)}",
        f"      PLAYRATE {_fmt(item.playrate)} 1 0 -1 0 0.0025",
        "      CHANMODE 0",
        f"      GUID {guid}",
        f"      <SOURCE {item.source_kind}",
        f'        FILE "{item.source_file}"',
        "      >",
        "    >",
    ))


def _render_track(track: Track, episode_code: str) -> str:
    guid = deterministic_guid(f"{episode_code}:track:{track.key}")
    mute = 1 if track.muted else 0
    mainsend = 1 if track.mainsend else 0

    lines = [
        f"  <TRACK {guid}",
        f"    NAME {_quoted(track.name)}",
        "    PEAKCOL 16576",
        "    BEAT -1",
        "    AUTOMODE 0",
        "    PANLAWFLAGS 3",
        "    VOLPAN 1 0 -1 -1 1",
        f"    MUTESOLO {mute} 0 0",
        "    IPHASE 0",
        "    PLAYOFFS 0 1",
        "    ISBUS 0 0",
        "    BUSCOMP 0 0 0 0 0",
        "    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0",
        "    FIXEDLANES 9 0 0 0 0",
        "    LANEREC -1 -1 -1 0",
        "    SEL 0",
        "    REC 0 0 1 0 0 0 0 0",
        "    VU 64",
        "    TRACKHEIGHT 25 0 0 0 0 0 0",
        "    INQ 0 0 0 0.5 100 0 0 100",
        "    NCHAN 2",
        "    FX 1",
        f"    TRACKID {guid}",
        "    PERF 0",
        "    MIDIOUT -1",
        f"    MAINSEND {mainsend} 0",
    ]

    if track.vol_points:
        env_guid = deterministic_guid(f"{episode_code}:volenv:{track.key}")
        lines.append("    <VOLENV2")
        lines.append(f"      EGUID {env_guid}")
        lines.append("      ACT 1 -1")
        lines.append("      VIS 1 1 1")
        lines.append("      LANEHEIGHT 0 0")
        lines.append("      ARM 1")
        lines.append("      DEFSHAPE 0 -1 -1")
        lines.append("      VOLTYPE 1")
        for pt in track.vol_points:
            lines.append(f"      PT {_fmt(pt.time)} {_fmt(pt.gain)} {pt.shape}")
        lines.append("    >")

    if track.fx_lines:
        lines.append("    <FXCHAIN")
        lines.append("      SHOW 0")
        lines.append("      LASTSEL 0")
        lines.append("      DOCKED 0")
        lines.extend(track.fx_lines)
        lines.append("    >")

    for i, item in enumerate(track.items):
        lines.append(_render_item(item, episode_code, track.key, i))

    lines.append("  >")
    return "\n".join(lines)


_HEADER_TEMPLATE = """<REAPER_PROJECT 0.1 "{version}" {project_id} 0
  <NOTES 0 2
  >
  RIPPLE 0 0
  GROUPOVERRIDE 0 0 0 0
  AUTOXFADE 129
  ENVATTACH 3
  POOLEDENVATTACH 0
  TCPUIFLAGS 0
  MIXERUIFLAGS 11 48
  ENVFADESZ10 40
  PEAKGAIN 1
  FEEDBACK 0
  PANLAW 1
  PROJOFFS 0 0 0
  MAXPROJLEN 0 0
  GRID 3455 8 1 8 1 0 0 0
  TIMEMODE 1 0 -1 24 2 0 -1 0
  VIDEO_CONFIG 0 0 65792
  PANMODE 3
  PANLAWFLAGS 3
  CURSOR 0
  ZOOM 1 0 0
  VZOOMEX 6.5 0
  USE_REC_CFG 0
  RECMODE 1
  SMPTESYNC 0 23.976024 100 40 1000 300 0 0 1 0 0
  LOOP 0
  LOOPGRAN 0 4
  RECORD_PATH "Media" ""
  <RECORD_CFG
    ZXZhdxgAAQ==
  >
  <APPLYFX_CFG
  >
  RENDER_FILE "{render_file}"
  RENDER_FMT 0 2 0
  RENDER_1X 0
  RENDER_RANGE 1 0 0 0 1000
  RENDER_RESAMPLE 3 0 1
  RENDER_ADDTOPROJ 0
  RENDER_STEMS 0
  RENDER_DITHER 0
  RENDER_TRIM 0.000001 0.000001 0 0
  TIMELOCKMODE 0
  TEMPOENVLOCKMODE 1
  ITEMMIX 1
  DEFPITCHMODE 589824 0
  MISCOPTS 4
  TAKELANE 1
  SAMPLERATE 48000 1 0
  <RENDER_CFG
    {render_cfg}
  >
  LOCK 1
  <METRONOME 6 2
    VOL 0.25 0.125
    BEATLEN 4
    FREQ 1760 880 1
    SAMPLES "" "" "" ""
    SPLIGNORE 0 0
    SPLDEF 2 660 "" 0 ""
    SPLDEF 3 440 "" 0 ""
    PATTERN 0 169
    PATTERNSTR ABBB
    MULT 1
  >
  GLOBAL_AUTO -1
  TEMPO 120 4 4 0
  PLAYRATE 1 0 0.25 4
  SELECTION 0 0
  SELECTION2 0 0
  MASTERAUTOMODE 0
  MASTERTRACKHEIGHT 0 0
  MASTERPEAKCOL 16576
  MASTERMUTESOLO 0
  MASTERTRACKVIEW 0 0.6667 0.5 0.5 0 0 0 0 0 0 0 0 0 0 1
  MASTERHWOUT 0 0 1 0 0 0 0 -1
  MASTER_NCH 2 2
  MASTER_VOLUME 1 0 -1 -1 1
  MASTER_PANMODE 3
  MASTER_PANLAWFLAGS 3
  MASTER_FX 1
  MASTER_SEL 0
  <MASTERPLAYSPEEDENV
    EGUID {master_playspeed_guid}
    ACT 0 -1
    VIS 0 1 1
    LANEHEIGHT 0 0
    ARM 0
    DEFSHAPE 0 -1 -1
  >
  <TEMPOENVEX
    EGUID {tempoenvex_guid}
    ACT 0 -1
    VIS 1 0 1
    LANEHEIGHT 0 0
    ARM 0
    DEFSHAPE 1 -1 -1
  >
  RULERHEIGHT 86 86
  RULERLANE 1 4 "" 0 -1 0
  RULERLANE 2 8 "" 0 -1 0
  <PROJBAY
  >"""


def build_project(project: Project) -> str:
    """Serializa `project` a texto .RPP completo, listo para escribir a disco."""
    header = _HEADER_TEMPLATE.format(
        version=REAPER_VERSION_TAG,
        project_id=project.project_id,
        render_file=str(project.render.output_file),
        render_cfg=project.render.render_cfg,
        master_playspeed_guid=deterministic_guid(f"{project.episode_code}:master_playspeed"),
        tempoenvex_guid=deterministic_guid(f"{project.episode_code}:tempoenvex"),
    )
    tracks_text = "\n".join(_render_track(t, project.episode_code) for t in project.tracks)
    return f"{header}\n{tracks_text}\n>\n"


def write_project(project: Project, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_project(project), encoding="utf-8", newline="\n")
