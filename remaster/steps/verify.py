"""Paso de verificacion: re-analiza el proyecto TAL COMO LO DEJO EL USUARIO.

El paso `loudness` calcula la ganancia global y la envolvente por escena
ANTES de que nadie toque el proyecto, suponiendo (a) que ES VOX es un
unico bloque desplazado por un offset + deriva constantes y (b) que todo
lo que suena en ES VOX sale de `es_vocals.flac`. En cuanto el proyecto se
edita a mano las dos suposiciones dejan de valer: el sync se afina
partiendo la pista en decenas o cientos de items, y los huecos se
rellenan con lo que suene mejor — un trozo de `en_vocals.flac`, una
pista mejor aislada, lo que haga falta.

Ninguna de esas dos cosas es un error: es como se consigue el resultado
bueno. Lo que si es un problema es que la envolvente calculada para
compensar que el castellano estaba bajo se siga aplicando encima de un
trozo que YA venia al nivel del ingles — ahi la ganancia se suma dos
veces. Por eso este paso no comprueba de que fichero sale cada item
(seria marcar como fallo justo lo que el usuario quiere hacer), sino el
NIVEL RESULTANTE de cada item contra EN VOX en ese mismo instante. La
fuente se reporta solo como contexto, para entender el porque.

Dos entradas, con papeles distintos:

  * el `.RPP` + las fuentes: exacto y por item, hasta los items de medio
    segundo. No ve ReaEQ/ReaFir, que van despues en la cadena.
  * el `render_stats.html` de un dry run de REAPER (opcional): la unica
    medida POST-FX. Resolucion gruesa (~5 s), pero es la verdad sobre el
    pico y sobre el "impuesto" que la cadena de FX suma al nivel.

Se usan las dos: la primera para el detalle por item, la segunda para el
global, el pico y el veredicto sobre la envolvente.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from ..audio.lufs import LufsError, measure_lufs
from ..layout import EpisodeLayout
from ..profile import Profile
from ..reaper.render_stats import RenderStatsError, TrackStats, find_stats, parse_render_stats
from ..reaper.rpp_read import (
    ReadTrack,
    envelope_max_db,
    envelope_mean_db,
    find_track,
    load_rpp,
    read_tracks,
)

MIN_ITEM_SECONDS = 0.5  # pyloudnorm no mide bloques mas cortos
WINDOW_SECONDS = 120.0  # ventana del analisis de deriva
SPEECH_PERCENTILE = 90  # nivel de dialogo robusto: ignora pausas y desfases de fraseo
SILENCE_FLOOR_LUFS = -40.0  # por debajo, la comparacion punto a punto es ruido
MAX_HOTSPOTS = 15
# Un item solo se puede juzgar si en ese mismo tramo hay dialogo ingles
# con el que compararlo. Por debajo de (LUFS-I de EN VOX - 12 dB) no lo
# hay: el ingles calla y el doblaje habla, cosa normalisima porque el
# fraseo no coincide. Sin este corte, la mitad de los items salen
# "desviados +14 dB" y el informe se vuelve inservible — comprobado.
EN_REFERENCE_MARGIN_DB = 12.0
FALLBACK_EN_LUFS_I = -23.0  # si no hay render_stats de donde sacar el real

# Margen para considerar que un item saca su audio del mismo sitio del
# mismo fichero que la pista inglesa, o sea que ES el ingles puesto en su
# sitio. Medio segundo: por debajo de eso ya es el mismo contenido.
SAME_CONTENT_TOLERANCE_S = 0.5

STATUS_OK = "ok"
STATUS_OFF = "desviado"
STATUS_NO_REFERENCE = "sin referencia inglesa"
STATUS_SHORT = "demasiado corto"
STATUS_SILENT = "sin audio medible"
STATUS_DOUBLE_GAIN = "ingles con ganancia encima"


class VerifyError(RuntimeError):
    pass


def format_timecode(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:06.3f}"


# --- resultados ---


@dataclass(frozen=True)
class ItemCheck:
    index: int
    start: float
    end: float
    source: str
    envelope_db: float  # media sobre el item: lo que le hace al nivel
    envelope_max_db: float  # maximo sobre el item: lo que le hace al pico
    item_gain_db: float
    global_gain_db: float
    total_gain_db: float
    es_lufs: float
    en_lufs: float
    delta_db: float
    peak_dbfs: float
    status: str = STATUS_OK
    note: str = ""

    @property
    def timecode(self) -> str:
        return format_timecode(self.start)


@dataclass(frozen=True)
class WindowCheck:
    start: float
    end: float
    es_level: float
    en_level: float
    delta_db: float


@dataclass(frozen=True)
class Hotspot:
    time: float
    es_lufs_s: float
    en_lufs_s: float
    delta_db: float


@dataclass(frozen=True)
class GlobalCheck:
    es_lufs_i: float
    en_lufs_i: float
    delta_db: float
    es_peak_dbfs: float
    en_peak_dbfs: float
    es_clips: int
    es_lra: float
    en_lra: float
    headroom_db: float


@dataclass(frozen=True)
class EnvelopeCheck:
    """A/B de la envolvente: que pasaria si no estuviese. Responde a la
    pregunta que uno se hace tras re-sincronizar a mano — si la
    envolvente se calculo contra un alineamiento que ya no existe,
    ¿sigue ayudando o ya estorba?
    """
    sd_with: float
    sd_without: float
    windows_out_with: int
    windows_out_without: int
    windows_total: int
    verdict: str


@dataclass(frozen=True)
class Action:
    """Una cosa concreta que hacer, en cristiano.

    El informe tenia todos los datos pero no decia que hacer con ellos.
    Cada hallazgo tecnico se traduce aqui a: que pasa, por que importa, y
    los pasos exactos en REAPER. Lo tecnico sigue abajo para quien quiera
    comprobarlo.
    """
    urgency: str  # "alta" | "media" | "info"
    title: str
    why: str
    how: str
    spots: tuple[str, ...] = ()


@dataclass(frozen=True)
class FxTax:
    """Cuanto sube el pico la cadena de FX (ReaEQ/ReaFir) respecto al pico
    de la señal cruda ya con las ganancias aplicadas. Es el numero que
    hace falta para fijar `peak_ceiling_dbfs` con datos.
    """
    predicted_peak_dbfs: float
    measured_peak_dbfs: float
    tax_db: float


@dataclass
class VerifyReport:
    episode: str
    rpp_path: str
    generated_at: str
    tolerance_db: float
    item_tolerance_db: float
    es_track: str
    en_track: str
    global_gain_db: float | None
    envelope_points: int
    item_count: int
    muted_items: int
    sources: tuple[str, ...]
    fx_names: tuple[str, ...]
    items: tuple[ItemCheck, ...] = ()
    stats_path: str | None = None
    global_check: GlobalCheck | None = None
    windows: tuple[WindowCheck, ...] = ()
    hotspots: tuple[Hotspot, ...] = ()
    envelope: EnvelopeCheck | None = None
    fx_tax: FxTax | None = None
    en_reference_floor_lufs: float = FALLBACK_EN_LUFS_I - EN_REFERENCE_MARGIN_DB
    rpp_modified: str = ""
    stats_modified: str = ""
    stats_is_stale: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def flagged_items(self) -> tuple[ItemCheck, ...]:
        return tuple(i for i in self.items if i.status in (STATUS_OFF, STATUS_DOUBLE_GAIN))

    @property
    def comparable_items(self) -> tuple[ItemCheck, ...]:
        return tuple(i for i in self.items if i.status in (STATUS_OK, STATUS_OFF, STATUS_DOUBLE_GAIN))

    @property
    def flagged_windows(self) -> tuple[WindowCheck, ...]:
        return tuple(w for w in self.windows if abs(w.delta_db) > self.tolerance_db)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sources"] = list(self.sources)
        data["fx_names"] = list(self.fx_names)
        return data


# --- lectura de audio ---


def _read_segment(path: Path, start_s: float, duration_s: float) -> tuple[np.ndarray, int, float]:
    """(mono, sample_rate, pico_real_dbfs) de un tramo del fichero. El pico
    se mide sobre los canales sin mezclar — mezclarlos lo esconde (ver
    audio/envelope.py:load_mono_and_peak_frames).
    """
    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        start = max(0, int(start_s * sr))
        frames = max(0, int(duration_s * sr))
        if start >= len(f) or frames == 0:
            return np.zeros(0, dtype=np.float32), sr, float("-inf")
        f.seek(start)
        data = f.read(frames=min(frames, len(f) - start), dtype="float32", always_2d=True)
    if data.size == 0:
        return np.zeros(0, dtype=np.float32), sr, float("-inf")
    peak = float(np.max(np.abs(data)))
    peak_db = 20 * math.log10(peak) if peak > 0 else float("-inf")
    return data.mean(axis=1), sr, peak_db


def _read_track_span(track: ReadTrack, start_s: float, end_s: float) -> tuple[np.ndarray, int] | None:
    """Reconstruye en mono lo que suena en la pista entre `start_s` y
    `end_s`, item a item. Los huecos quedan en silencio, que es lo que
    suena de verdad.
    """
    duration = end_s - start_s
    if duration <= 0:
        return None
    sr = None
    buffer: np.ndarray | None = None
    for item in track.items:
        if item.muted or item.source_file is None or item.end <= start_s or item.position >= end_s:
            continue
        span_start = max(start_s, item.position)
        span_end = min(end_s, item.end)
        mono, item_sr, _ = _read_segment(
            item.source_file, item.source_time_at(span_start), (span_end - span_start) * item.playrate
        )
        if mono.size == 0:
            continue
        if sr is None:
            sr = item_sr
            buffer = np.zeros(int(duration * sr), dtype=np.float32)
        elif item_sr != sr:
            continue
        assert buffer is not None
        offset = int((span_start - start_s) * sr)
        take = min(len(mono), len(buffer) - offset)
        if take > 0:
            buffer[offset:offset + take] += mono[:take] * item.volume
    if buffer is None or sr is None:
        return None
    return buffer, sr


# --- analisis por item (desde el RPP) ---


def _english_placement(en_track: ReadTrack) -> tuple[Path | None, float]:
    """(fichero fuente de la pista inglesa, desplazamiento que le aplica).

    El desplazamiento es `position - soffs`, o sea cuanto se corre el
    fichero respecto a la linea de tiempo. Se toma la mediana de los
    items porque la pista se parte en trozos para corregir deriva y algun
    trozo puede ir unos milisegundos movido.
    """
    offsets: list[float] = []
    source: Path | None = None
    for item in en_track.items:
        if item.source_file is None or item.muted:
            continue
        source = source or item.source_file
        if item.source_file == source:
            offsets.append(item.position - item.soffs)
    if source is None or not offsets:
        return None, 0.0
    return source, float(sorted(offsets)[len(offsets) // 2])


def _is_english_in_place(item, en_source: Path | None, en_offset: float) -> bool:
    """¿Este item es el audio ingles sonando en su propio instante?

    Se comprueba contra el MAPEO de la pista inglesa (misma fuente, mismo
    `position - soffs`) y no contra sus items, a proposito: la pista
    inglesa tiene huecos, y los parches del usuario caen justo en ellos —
    se rellena el castellano donde no lo hay, que suele ser donde el
    ingles tampoco tiene item. Buscando "el item de EN VOX que solape"
    estos casos daban None y acababan en "sin referencia inglesa", que es
    precisamente donde mas falta hace mirar.

    Importa porque es el unico caso con nivel correcto conocido: si el
    audio ya es el ingles, la ganancia buena es 0 dB y lo que aplique la
    envolvente es desequilibrio puro, sin ruido de medida de por medio.
    """
    if en_source is None or item.source_file is None or item.source_file != en_source:
        return False
    return abs((item.position - item.soffs) - en_offset) <= SAME_CONTENT_TOLERANCE_S


def _check_items(
    es_track: ReadTrack, en_track: ReadTrack, global_gain_db: float,
    en_floor_lufs: float, tolerance_db: float, in_place_tolerance_db: float,
    warnings: list[str],
) -> tuple[ItemCheck, ...]:
    checks: list[ItemCheck] = []
    en_source, en_offset = _english_placement(en_track)
    for index, item in enumerate(es_track.items):
        if item.muted or item.source_file is None:
            continue
        if not item.source_file.exists():
            warnings.append(f"item {index} en {format_timecode(item.position)}: falta la fuente {item.source_file}")
            continue

        envelope_db = envelope_mean_db(es_track.vol_points, item.position, item.end)
        envelope_peak_db = envelope_max_db(es_track.vol_points, item.position, item.end)
        item_gain_db = 20 * math.log10(item.volume) if item.volume > 0 else -120.0
        total_gain_db = global_gain_db + envelope_db + item_gain_db

        es_mono, es_sr, raw_peak_db = _read_segment(
            item.source_file, item.soffs, item.length * item.playrate
        )
        peak_gain_db = global_gain_db + envelope_peak_db + item_gain_db
        peak_dbfs = raw_peak_db + peak_gain_db if math.isfinite(raw_peak_db) else float("-inf")

        status, note = STATUS_OK, ""
        es_lufs = en_lufs = delta = float("nan")
        english_in_place = _is_english_in_place(item, en_source, en_offset)
        if english_in_place:
            # No hace falta medir nada: el audio ya ES el ingles, luego la
            # ganancia aplicada es exactamente el desequilibrio. Umbral mas
            # estrecho que el de los items normales porque aqui no hay
            # ruido de medida que tolerar.
            off = abs(total_gain_db) > in_place_tolerance_db
            checks.append(ItemCheck(
                index=index, start=item.position, end=item.end, source=item.source_file.name,
                envelope_db=round(envelope_db, 2), envelope_max_db=round(envelope_peak_db, 2),
                item_gain_db=round(item_gain_db, 2), global_gain_db=round(global_gain_db, 2),
                total_gain_db=round(total_gain_db, 2),
                es_lufs=float("nan"), en_lufs=float("nan"), delta_db=round(total_gain_db, 2),
                peak_dbfs=round(peak_dbfs, 2) if math.isfinite(peak_dbfs) else float("-inf"),
                status=STATUS_DOUBLE_GAIN if off else STATUS_OK,
                note=(f"es el ingles en su sitio, pero le caen {total_gain_db:+.2f} dB encima: "
                      "suena asi de descuadrado frente a la mezcla original")
                if off else "es el ingles en su sitio, con ganancia despreciable",
            ))
            continue
        if item.length < MIN_ITEM_SECONDS:
            status = STATUS_SHORT
            note = f"{item.length:.2f}s: por debajo del bloque minimo de LUFS"
        else:
            en_span = _read_track_span(en_track, item.position, item.end)
            measured_es = measured_en = float("nan")
            measurable = True
            try:
                measured_es = measure_lufs(es_mono, es_sr) if es_mono.size else float("-inf")
                measured_en = measure_lufs(en_span[0], en_span[1]) if en_span else float("-inf")
            except LufsError as exc:
                status, note, measurable = STATUS_SHORT, str(exc), False
            if not measurable:
                pass
            elif not math.isfinite(measured_en) or measured_en < en_floor_lufs:
                status = STATUS_NO_REFERENCE
                note = "el ingles calla en este tramo: no hay con que comparar"
                en_lufs = round(measured_en, 2) if math.isfinite(measured_en) else float("-inf")
            elif not math.isfinite(measured_es):
                status = STATUS_SILENT
                note = "el item no tiene señal medible aunque el ingles habla"
                en_lufs = round(measured_en, 2)
            else:
                es_lufs = measured_es + total_gain_db
                en_lufs = measured_en
                delta = es_lufs - en_lufs
                status = STATUS_OFF if abs(delta) > tolerance_db else STATUS_OK

        checks.append(ItemCheck(
            index=index, start=item.position, end=item.end,
            source=item.source_file.name,
            envelope_db=round(envelope_db, 2), envelope_max_db=round(envelope_peak_db, 2),
            item_gain_db=round(item_gain_db, 2),
            global_gain_db=round(global_gain_db, 2), total_gain_db=round(total_gain_db, 2),
            es_lufs=round(es_lufs, 2), en_lufs=round(en_lufs, 2), delta_db=round(delta, 2),
            peak_dbfs=round(peak_dbfs, 2) if math.isfinite(peak_dbfs) else float("-inf"),
            status=status, note=note,
        ))
    return tuple(checks)


# --- analisis temporal (desde render_stats.html) ---


def _envelope_series(track: ReadTrack, times: np.ndarray) -> np.ndarray:
    if not track.vol_points:
        return np.zeros(len(times))
    pts = np.array(track.vol_points)
    gains = np.interp(times, pts[:, 0], pts[:, 1])
    return 20 * np.log10(np.maximum(gains, 1e-6))


def _windows(times: np.ndarray, es: np.ndarray, en: np.ndarray) -> tuple[WindowCheck, ...]:
    out: list[WindowCheck] = []
    if len(times) == 0:
        return ()
    for start in np.arange(0.0, float(times[-1]), WINDOW_SECONDS):
        selected = (times >= start) & (times < start + WINDOW_SECONDS)
        es_window = es[selected][np.isfinite(es[selected])]
        en_window = en[selected][np.isfinite(en[selected])]
        if len(es_window) < 5 or len(en_window) < 5:
            continue
        es_level = float(np.percentile(es_window, SPEECH_PERCENTILE))
        en_level = float(np.percentile(en_window, SPEECH_PERCENTILE))
        out.append(WindowCheck(
            start=float(start), end=float(start + WINDOW_SECONDS),
            es_level=round(es_level, 2), en_level=round(en_level, 2),
            delta_db=round(es_level - en_level, 2),
        ))
    return tuple(out)


def _window_spread(times: np.ndarray, es: np.ndarray, en: np.ndarray, tolerance_db: float) -> tuple[float, int, int]:
    windows = _windows(times, es, en)
    if not windows:
        return float("nan"), 0, 0
    deltas = np.array([w.delta_db for w in windows])
    return float(np.std(deltas)), int(np.sum(np.abs(deltas) > tolerance_db)), len(windows)


def _hotspots(times: np.ndarray, es: np.ndarray, en: np.ndarray) -> tuple[Hotspot, ...]:
    mask = np.isfinite(es) & np.isfinite(en) & (es > SILENCE_FLOOR_LUFS) & (en > SILENCE_FLOOR_LUFS)
    if not mask.any():
        return ()
    delta = es - en
    indices = np.where(mask)[0]
    ordered = indices[np.argsort(-np.abs(delta[indices]))][:MAX_HOTSPOTS]
    return tuple(
        Hotspot(time=float(times[i]), es_lufs_s=float(es[i]), en_lufs_s=float(en[i]), delta_db=round(float(delta[i]), 2))
        for i in sorted(ordered, key=lambda i: -abs(delta[i]))
    )


def _analyse_stats(
    report: VerifyReport, es_track: ReadTrack, stats: tuple[TrackStats, ...],
    es_name: str, en_name: str, tolerance_db: float,
) -> None:
    es_stats = find_stats(stats, es_name)
    en_stats = find_stats(stats, en_name)
    if es_stats is None or en_stats is None:
        report.warnings.append(
            f"render_stats sin filas para '{es_name}' y '{en_name}' "
            f"(encontradas: {', '.join(t.name for t in stats)}). "
            "Haz el dry run con las dos pistas de voz sin mutear."
        )
        return

    report.global_check = GlobalCheck(
        es_lufs_i=es_stats.lufs_i, en_lufs_i=en_stats.lufs_i,
        delta_db=round(es_stats.lufs_i - en_stats.lufs_i, 2),
        es_peak_dbfs=es_stats.peak_dbfs, en_peak_dbfs=en_stats.peak_dbfs,
        es_clips=es_stats.clips, es_lra=es_stats.lra, en_lra=en_stats.lra,
        headroom_db=round(-es_stats.peak_dbfs, 2),
    )

    if len(es_stats.times) != len(en_stats.times) or not es_stats.times:
        report.warnings.append("las series temporales de ES y EN no cuadran; me salto ventanas y picos puntuales")
        return

    times = np.array(es_stats.times)
    es_series = np.array(es_stats.lufs_s)
    en_series = np.array(en_stats.lufs_s)

    report.windows = _windows(times, es_series, en_series)
    report.hotspots = _hotspots(times, es_series, en_series)

    envelope_db = _envelope_series(es_track, times)
    sd_with, out_with, total = _window_spread(times, es_series, en_series, tolerance_db)
    sd_without, out_without, _ = _window_spread(times, es_series - envelope_db, en_series, tolerance_db)
    if total:
        if not math.isfinite(sd_with) or not math.isfinite(sd_without):
            verdict = "sin datos suficientes"
        elif sd_with < sd_without:
            verdict = "la envolvente sigue ayudando: dejala"
        elif sd_with > sd_without:
            verdict = "la envolvente ya estorba: recalcula el paso loudness o aplanala"
        else:
            verdict = "la envolvente es indiferente"
        report.envelope = EnvelopeCheck(
            sd_with=round(sd_with, 2), sd_without=round(sd_without, 2),
            windows_out_with=out_with, windows_out_without=out_without,
            windows_total=total, verdict=verdict,
        )

    predicted = max(
        (i.peak_dbfs for i in report.items if math.isfinite(i.peak_dbfs)), default=float("-inf")
    )
    if math.isfinite(predicted) and math.isfinite(es_stats.peak_dbfs):
        report.fx_tax = FxTax(
            predicted_peak_dbfs=round(predicted, 2),
            measured_peak_dbfs=es_stats.peak_dbfs,
            tax_db=round(es_stats.peak_dbfs - predicted, 2),
        )


# --- entrada publica ---


def verify_project(
    layout: EpisodeLayout,
    profile: Profile,
    stats_path: Path | None = None,
) -> VerifyReport:
    rpp_path = layout.rpp_edit
    if not rpp_path.exists():
        raise VerifyError(f"No hay proyecto que verificar en {rpp_path}. Ejecuta antes el paso 'project'.")

    tracks = read_tracks(load_rpp(rpp_path))
    es_name, en_name = profile.tracks.es_vox, profile.tracks.en_vox
    es_track = find_track(tracks, es_name)
    en_track = find_track(tracks, en_name)
    if es_track is None or en_track is None:
        found = ", ".join(t.name for t in tracks) or "ninguna"
        raise VerifyError(f"No encuentro las pistas '{es_name}' y '{en_name}' en {rpp_path.name} (hay: {found})")

    warnings: list[str] = []
    global_gain_db = es_track.js_volume_db
    if global_gain_db is None:
        warnings.append(
            f"'{es_name}' no tiene el FX 'JS: utility/volume'; supongo 0 dB de ganancia global"
        )
        global_gain_db = 0.0

    report = VerifyReport(
        episode=layout.code,
        rpp_path=str(rpp_path),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        tolerance_db=profile.loudness.verify_tolerance_db,
        item_tolerance_db=profile.loudness.verify_item_tolerance_db,
        es_track=es_name, en_track=en_name,
        global_gain_db=global_gain_db,
        envelope_points=len(es_track.vol_points),
        item_count=len(es_track.items),
        muted_items=sum(1 for i in es_track.items if i.muted),
        sources=es_track.sources(),
        fx_names=es_track.fx_names,
        warnings=warnings,
    )
    # Las stats se leen ANTES de los items: de ellas sale el LUFS-I real de
    # EN VOX, y de ahi el suelo por debajo del cual un tramo ingles no
    # sirve como referencia (ver EN_REFERENCE_MARGIN_DB).
    stats: tuple[TrackStats, ...] = ()
    en_lufs_i = FALLBACK_EN_LUFS_I
    if stats_path is not None:
        try:
            stats = parse_render_stats(stats_path)
        except (OSError, RenderStatsError) as exc:
            warnings.append(f"no pude leer {stats_path}: {exc}")
        else:
            report.stats_path = str(stats_path)
            # Si el proyecto se guardo DESPUES del dry run, la parte
            # post-FX del informe describe una version que ya no existe.
            # Es la primera pregunta que se hace cualquiera al abrirlo
            # ("¿tengo que volver a analizar?"), asi que se responde sola.
            stats_mtime = stats_path.stat().st_mtime
            rpp_mtime = rpp_path.stat().st_mtime
            report.stats_modified = datetime.fromtimestamp(stats_mtime).strftime("%d/%m/%Y %H:%M")
            report.rpp_modified = datetime.fromtimestamp(rpp_mtime).strftime("%d/%m/%Y %H:%M")
            report.stats_is_stale = stats_mtime < rpp_mtime
            en_stats = find_stats(stats, en_name)
            if en_stats is not None and math.isfinite(en_stats.lufs_i):
                en_lufs_i = en_stats.lufs_i
    else:
        warnings.append(
            "sin medida de REAPER: faltan el nivel global, el pico real y el veredicto "
            "sobre la envolvente. En REAPER, selecciona las pistas a analizar y ejecuta la "
            "accion 'Calculate loudness of selected tracks via dry run render'."
        )

    report.en_reference_floor_lufs = round(en_lufs_i - EN_REFERENCE_MARGIN_DB, 2)
    report.items = _check_items(
        es_track, en_track, global_gain_db, report.en_reference_floor_lufs,
        report.item_tolerance_db, report.tolerance_db, warnings,
    )

    if stats:
        _analyse_stats(report, es_track, stats, es_name, en_name, report.tolerance_db)

    return report



# --- traduccion de hallazgos a cosas que hacer ---

REAPER_DRY_RUN_STEPS = (
    "En REAPER: abre el proyecto, selecciona las pistas que quieras analizar "
    "(«ES VOX» y «EN VOX») y ejecuta la acción "
    "«Calculate loudness of selected tracks via dry run render». Al terminar, "
    "vuelve aquí y pulsa «Re-analizar»."
)
# Margen que se considera comodo entre el pico y 0 dBFS. Por debajo de
# esto, cualquier retoque posterior puede hacer que el audio sature.
COMFORTABLE_HEADROOM_DB = 3.0
MAX_SPOTS_LISTED = 20


def _plain_direction(delta_db: float) -> str:
    return "más alto" if delta_db > 0 else "más bajo"


def _spot(start: float, end: float, delta_db: float) -> str:
    """Una linea de tramo, siempre con la misma forma:

        24:05.318 → 24:06.718 (1.4s): ES sube 13.7 dB (suena más bajo que EN)

    Primero DONDE (con final, para poder seleccionar el tramo en REAPER
    sin calcular nada), luego QUE HACER, y solo al final el porque. El
    formato anterior daba la observacion pero no la accion, y obligaba a
    invertir el signo mentalmente: "suena 13.7 dB mas bajo" -> ¿subo o
    bajo, y cuanto?
    """
    verb = "baja" if delta_db > 0 else "sube"
    return (
        f"{format_timecode(start)} → {format_timecode(end)} ({end - start:.1f}s): "
        f"ES {verb} {abs(delta_db):.1f} dB (suena {_plain_direction(delta_db)} que EN)"
    )



def _spots_in_playback_order(findings: list, limit: int | None = None) -> tuple[str, ...]:
    """Los `limit` tramos mas graves, pero listados de principio a fin.

    Recortar y ordenar son dos decisiones distintas. Se recorta por
    gravedad (si hay que dejar fuera algo, que sea lo menos importante) y
    se lista por tiempo, que es como se revisa: del minuto 4 al 48
    seguidos, sin ir saltando por el episodio.
    """
    worst = sorted(findings, key=lambda f: -abs(f.delta_db))
    if limit is not None:
        worst = worst[:limit]
    return tuple(_spot(f.start, f.end, f.delta_db) for f in sorted(worst, key=lambda f: f.start))


def build_actions(report: VerifyReport) -> tuple[Action, ...]:
    actions: list[Action] = []

    if report.stats_path is None:
        actions.append(Action(
            urgency="alta",
            title="Falta la medida de REAPER: este informe está a medias",
            why="Sin ella no se puede saber el volumen global real, ni cuánto margen queda "
                "antes de que el audio sature, ni si la envolvente sigue haciendo bien su trabajo.",
            how=REAPER_DRY_RUN_STEPS,
        ))
    elif report.stats_is_stale:
        actions.append(Action(
            urgency="alta",
            title="Vuelve a medir: has tocado el proyecto después del último análisis",
            why=f"La medida de REAPER es del {report.stats_modified} y el proyecto se guardó "
                f"el {report.rpp_modified}. La parte de volumen global y pico describe una "
                "versión que ya no existe. El detalle por tramos sí está al día.",
            how=REAPER_DRY_RUN_STEPS,
        ))

    doubled = [i for i in report.items if i.status == STATUS_DOUBLE_GAIN]
    if doubled:
        worst = max(abs(i.delta_db) for i in doubled)
        actions.append(Action(
            urgency="alta",
            title=f"Baja el volumen a {len(doubled)} trozos que en realidad son audio en inglés",
            why="Son trozos del inglés colocados en su propio sitio, de los que usas para tapar "
                "huecos. El problema es que encima les cae la subida de volumen calculada para el "
                f"castellano, que ahí sobra: suenan hasta {worst:.1f} dB más fuerte que en la "
                "película original. Se nota como un salto de volumen.",
            how="En REAPER, en la envolvente de volumen de la pista «ES VOX», selecciona cada uno "
                "de estos tramos y ponlo a 0 dB (o baja los dB que se indican).",
            spots=_spots_in_playback_order(doubled),
        ))

    off = [i for i in report.items if i.status == STATUS_OFF]
    if off:
        actions.append(Action(
            urgency="media",
            title=f"Escucha {len(off)} momentos donde el castellano se sale de nivel",
            why="Comparados con el inglés de ese mismo instante, suenan más alto o más bajo de la "
                "cuenta. Ojo: no todos son un fallo. A veces es simplemente que la frase doblada "
                "no dura lo mismo que la inglesa. Por eso hay que escucharlos, no corregirlos a ciegas.",
            how="Van en orden de principio a fin, para que puedas revisarlos del tirón. Ve al "
                "minuto indicado, escucha, y si de verdad canta, ajusta la envolvente de "
                "«ES VOX» en ese tramo los dB que dice la lista.",
            spots=_spots_in_playback_order(off, MAX_SPOTS_LISTED),
        ))

    g = report.global_check
    if g and g.headroom_db < COMFORTABLE_HEADROOM_DB:
        current = report.global_gain_db or 0.0
        suggested = current - (COMFORTABLE_HEADROOM_DB - g.headroom_db)
        # El nivel de alarma depende de si YA esta roto o solo es
        # arriesgado. Si el contador de recortes esta a cero, el fichero
        # no distorsiona: es una precaucion, no una averia, y decirlo
        # como si lo fuera hace que se ignore el aviso cuando si importe.
        if g.es_clips:
            urgency = "alta"
            why = (
                f"El momento más fuerte de «{report.es_track}» llega a {g.es_peak_dbfs:.1f} dBFS y "
                f"hay {g.es_clips} muestras recortadas: eso ya es distorsión audible, no un riesgo."
            )
        else:
            urgency = "media" if g.headroom_db < 1.0 else "info"
            why = (
                f"En audio digital el máximo absoluto es 0 dBFS: por encima de ahí no hay números, "
                f"la onda se corta en plano y suena a chasquido. El momento más fuerte de "
                f"«{report.es_track}» llega a {g.es_peak_dbfs:.1f} dBFS, o sea a "
                f"{g.headroom_db:.1f} dB del techo. "
                f"Ninguna muestra está recortada, así que el fichero tal cual NO distorsiona. "
                f"El margen es por lo que viene después: al reproducir, el reconstruir la onda entre "
                f"muestras puede subir un poco por encima del pico medido, y si algún día conviertes "
                f"a un formato con pérdida (AAC, Opus) el codificador puede pasarse otro tanto. "
                f"Por eso la norma habitual (EBU R128) deja 1 dB de aire. "
                f"Si no vas a tocar nada más, puedes dejarlo: es precaución, no avería."
            )
        actions.append(Action(
            urgency=urgency,
            title="Deja más hueco antes de que el audio sature",
            why=why,
            how=f"Si quieres el margen: en la pista «{report.es_track}», abre el efecto "
                f"«JS: utility/volume» y baja «Adjustment» de {current:+.2f} dB a "
                f"{suggested:+.2f} dB. Luego vuelve a medir para comprobarlo. "
                f"Ojo: esto mide solo la pista de voz. La mezcla final le suma encima el fondo "
                f"musical, así que el pico de verdad puede ser algo más alto.",
        ))

    if report.flagged_windows:
        actions.append(Action(
            urgency="media",
            title=f"Revisa {len(report.flagged_windows)} tramos largos que quedan descompensados",
            why="No es un momento suelto: durante dos minutos seguidos el castellano queda por "
                "encima o por debajo del inglés. Eso sí se percibe como «esta parte suena rara».",
            how="Escucha el tramo entero y, si hace falta, mueve la envolvente de «ES VOX» en bloque.",
            spots=_spots_in_playback_order(list(report.flagged_windows)),
        ))

    if report.envelope:
        helping = report.envelope.sd_with < report.envelope.sd_without
        actions.append(Action(
            urgency="info",
            title="La envolvente de volumen: déjala como está" if helping
                  else "La envolvente de volumen ya no ayuda: conviene rehacerla",
            why="La subida de volumen automática se calculó antes de que sincronizaras a mano. "
                + ("Comprobado: aun así sigue acercando el castellano al inglés, más que si no estuviera."
                   if helping else
                   "Comprobado: tal y como está ahora deja el resultado PEOR que si no estuviera."),
            how="No tienes que hacer nada." if helping else
                "Vuelve a ejecutar el paso «loudness» con --force, o aplana la envolvente a 0 dB.",
        ))

    return tuple(actions)


def write_report(layout: EpisodeLayout, report: VerifyReport) -> tuple[Path, Path]:
    """Deja verify.json (para maquinas) y verify.html (para leer) en
    `.remaster/`. Devuelve las dos rutas.
    """
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    json_path = layout.state_dir / "verify.json"
    html_path = layout.state_dir / "verify.html"
    json_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    html_path.write_text(render_html(report))
    return json_path, html_path


# --- informe HTML ---


def _tone(delta: float, tolerance: float) -> str:
    if not math.isfinite(delta):
        return "muted"
    if abs(delta) > tolerance * 2:
        return "bad"
    if abs(delta) > tolerance:
        return "warn"
    return "ok"


def _item_tone(item: ItemCheck, tolerance: float) -> str:
    if item.status == STATUS_DOUBLE_GAIN:
        return "bad"
    if item.status == STATUS_OFF:
        return _tone(item.delta_db, tolerance)
    if item.status == STATUS_SILENT:
        return "warn"
    if item.status == STATUS_OK:
        return "ok"
    return "muted"


def _item_sort_key(item: ItemCheck) -> tuple[int, float]:
    """Primero lo accionable: los desviados por magnitud, luego los que no
    se han podido juzgar, luego los que estan bien.
    """
    if item.status == STATUS_DOUBLE_GAIN:
        return 0, -abs(item.delta_db)
    if item.status == STATUS_OFF:
        return 1, -abs(item.delta_db)
    if item.status == STATUS_SILENT:
        return 2, item.start
    if item.status == STATUS_OK:
        return 4, item.start
    return 3, item.start


def _fmt(value: float, suffix: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:+.2f}{suffix}" if suffix == " dB" else f"{value:.2f}{suffix}"




def _actions_html(actions: tuple[Action, ...]) -> str:
    if not actions:
        return "<p class='allgood'>Nada que tocar. El castellano cuadra con el inglés en todo el episodio.</p>"
    blocks = []
    for n, action in enumerate(actions, start=1):
        spots = ""
        if action.spots:
            rows = "".join(f"<li>{spot}</li>" for spot in action.spots)
            spots = f"<ol class='spots'>{rows}</ol>"
        blocks.append(f"""
    <div class="action {action.urgency}">
      <h3><span class="num">{n}</span>{action.title}</h3>
      <p class="why">{action.why}</p>
      <p class="how"><b>Qué hacer:</b> {action.how}</p>
      {spots}
    </div>""")
    return "\n".join(blocks)


def _summary_html(report: VerifyReport) -> str:
    rows = []
    g = report.global_check
    if g:
        verdict = ("cuadran" if abs(g.delta_db) <= 1.0 else
                   f"el castellano queda {abs(g.delta_db):.1f} dB {_plain_direction(g.delta_db)}")
        rows.append((
            "Volumen general del episodio",
            f"El castellano y el inglés {verdict}.",
            "ok" if abs(g.delta_db) <= 1.0 else "warn",
        ))
        rows.append((
            "Margen antes de saturar",
            f"Quedan {g.headroom_db:.1f} dB hasta el techo"
            + (f", y hay {g.es_clips} muestras recortadas." if g.es_clips else "."),
            "bad" if g.headroom_db < 1 else "warn" if g.headroom_db < COMFORTABLE_HEADROOM_DB else "ok",
        ))
    if report.envelope:
        helping = report.envelope.sd_with < report.envelope.sd_without
        rows.append((
            "Envolvente de volumen",
            "Sigue ayudando: déjala." if helping else "Ya no ayuda: conviene rehacerla.",
            "ok" if helping else "warn",
        ))
    rows.append((
        "Tramos a revisar",
        f"{len(report.flagged_items)} de {len(report.comparable_items)} comparables."
        if report.flagged_items else "Ninguno.",
        "warn" if report.flagged_items else "ok",
    ))
    cells = "".join(
        f"<tr class='{tone}'><td>{name}</td><td>{text}</td></tr>" for name, text, tone in rows
    )
    return f"<table class='summary-table'>{cells}</table>"


def _fx_html(report: VerifyReport) -> str:
    if not report.fx_tax:
        return ""
    t = report.fx_tax
    names = ", ".join(n for n in report.fx_names if not n.startswith("JS:")) or "los efectos"
    if t.tax_db < 0:
        effect = f"bajan el momento más fuerte {abs(t.tax_db):.1f} dB"
    else:
        effect = f"suben el momento más fuerte {t.tax_db:.1f} dB"
    warning = ""
    if t.predicted_peak_dbfs > 0:
        warning = (
            "<p class='danger'><b>Ojo con esto:</b> sin esos efectos el audio se saldría de escala "
            f"({t.predicted_peak_dbfs:+.1f} dBFS, y el maximo es 0). Ahora mismo son ellos los que "
            "están evitando que sature. Si los quitas, los cambias de sitio o tocas sus ajustes, "
            "vuelve a medir antes de dar el episodio por bueno.</p>"
        )
    return f"""
    <h3>Qué le hacen ReaEQ y ReaFir al volumen</h3>
    <p>Los efectos de la pista «{report.es_track}» ({names}) {effect}:
    de {t.predicted_peak_dbfs:.2f} dBFS antes de pasar por ellos a {t.measured_peak_dbfs:.2f} dBFS después.</p>
    {warning}
    <p class='muted'>Para técnicos: para que el render acabe con un pico objetivo P, pon
    <code>peak_ceiling_dbfs = P {-t.tax_db:+.2f}</code> en <code>[loudness]</code> del perfil y
    relanza el paso <code>loudness --force</code>.</p>"""


def render_html(report: VerifyReport) -> str:
    actions = build_actions(report)

    rows_items = "\n".join(
        f"<tr class='{_item_tone(i, report.item_tolerance_db)}'>"
        f"<td>{format_timecode(i.start)}</td><td>{i.end - i.start:.2f}s</td>"
        f"<td>{i.source}</td><td>{_fmt(i.envelope_db, ' dB')}</td>"
        f"<td>{_fmt(i.total_gain_db, ' dB')}</td><td>{_fmt(i.es_lufs)}</td>"
        f"<td>{_fmt(i.en_lufs)}</td><td><b>{_fmt(i.delta_db, ' dB')}</b></td>"
        f"<td>{_fmt(i.peak_dbfs)}</td><td class='note'>{i.note or i.status}</td></tr>"
        for i in sorted(report.items, key=_item_sort_key)
    )
    rows_windows = "\n".join(
        f"<tr class='{_tone(w.delta_db, report.tolerance_db)}'>"
        f"<td>{format_timecode(w.start)} – {format_timecode(w.end)}</td>"
        f"<td>{_fmt(w.es_level)}</td><td>{_fmt(w.en_level)}</td>"
        f"<td><b>{_fmt(w.delta_db, ' dB')}</b></td></tr>"
        for w in report.windows
    )
    rows_hotspots = "\n".join(
        f"<tr class='{_tone(h.delta_db, report.tolerance_db)}'>"
        f"<td>{format_timecode(h.time)}</td><td>{_fmt(h.es_lufs_s)}</td>"
        f"<td>{_fmt(h.en_lufs_s)}</td><td><b>{_fmt(h.delta_db, ' dB')}</b></td></tr>"
        for h in report.hotspots
    )

    g = report.global_check
    global_html = "<p class='muted'>Sin medida de REAPER — no hay cifras globales post-efectos.</p>"
    if g:
        global_html = f"""
    <table>
      <tr><th></th><th>{report.es_track}</th><th>{report.en_track}</th><th>delta</th></tr>
      <tr class='{_tone(g.delta_db, 1.0)}'><td>LUFS-I</td><td>{g.es_lufs_i:.1f}</td><td>{g.en_lufs_i:.1f}</td><td><b>{g.delta_db:+.2f} dB</b></td></tr>
      <tr><td>LRA</td><td>{g.es_lra:.1f}</td><td>{g.en_lra:.1f}</td><td>{g.es_lra - g.en_lra:+.1f}</td></tr>
      <tr class='{"bad" if g.headroom_db < 1 else "warn" if g.headroom_db < COMFORTABLE_HEADROOM_DB else "ok"}'>
        <td>Pico (dBFS)</td><td>{g.es_peak_dbfs:.1f}</td><td>{g.en_peak_dbfs:.1f}</td>
        <td><b>{g.headroom_db:.1f} dB de margen</b></td></tr>
      <tr class='{"bad" if g.es_clips else "ok"}'><td>Clips</td><td>{g.es_clips}</td><td>—</td><td></td></tr>
    </table>"""

    envelope_html = "<p class='muted'>Sin datos.</p>"
    if report.envelope:
        e = report.envelope
        envelope_html = f"""
    <p>Desviación típica del castellano respecto al inglés, por ventanas de {WINDOW_SECONDS:g}s:</p>
    <table>
      <tr><th></th><th>desviación típica</th><th>ventanas fuera de ±{report.tolerance_db:g} dB</th></tr>
      <tr><td>con la envolvente puesta</td><td>{e.sd_with:.2f} dB</td><td>{e.windows_out_with} de {e.windows_total}</td></tr>
      <tr><td>si se quitara</td><td>{e.sd_without:.2f} dB</td><td>{e.windows_out_without} de {e.windows_total}</td></tr>
    </table>
    <p><b>{e.verdict}</b></p>"""

    warnings_html = ""
    if report.warnings:
        items = "".join(f"<li>{w}</li>" for w in report.warnings)
        warnings_html = f"<div class='warnings'><h3>Avisos</h3><ul>{items}</ul></div>"

    stats_line = "sin medida de REAPER"
    if report.stats_path:
        stats_line = f"medida de REAPER del {report.stats_modified}"
        if report.stats_is_stale:
            stats_line += f" (anterior al último guardado del proyecto, {report.rpp_modified})"

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Revisión de {report.episode}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
         padding: 0 1rem; line-height: 1.5; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.2rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2.5rem; }}
  h3 {{ font-size: 1rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 0.6rem 0 1.4rem; font-size: 0.86rem; }}
  th, td {{ text-align: right; padding: 0.3rem 0.5rem; border-bottom: 1px solid #8883; }}
  th:first-child, td:first-child, td.note {{ text-align: left; }}
  td.note {{ color: #888; font-size: 0.8rem; }}
  tr.warn td {{ background: #f0ad4e22; }} tr.bad td {{ background: #d9534f33; }}
  tr.muted td {{ color: #888; }}
  .summary-table td {{ text-align: left; font-size: 0.95rem; }}
  .summary-table td:first-child {{ width: 15rem; color: #888; }}
  .muted {{ color: #888; font-size: 0.85rem; }}
  .action {{ border-left: 4px solid #8886; padding: 0.1rem 0 0.1rem 1rem; margin: 1.4rem 0; }}
  .action.alta {{ border-color: #d9534f; }}
  .action.media {{ border-color: #f0ad4e; }}
  .action.info {{ border-color: #5bc0de; }}
  .action h3 {{ margin: 0.4rem 0; }}
  .action .num {{ display: inline-block; width: 1.6rem; height: 1.6rem; line-height: 1.6rem;
                  text-align: center; border-radius: 50%; background: #8883; margin-right: 0.5rem;
                  font-size: 0.85rem; }}
  .action .why {{ margin: 0.3rem 0; }}
  .action .how {{ margin: 0.3rem 0; }}
  .spots {{ font-family: ui-monospace, monospace; font-size: 0.82rem; margin: 0.5rem 0 0;
            padding-left: 1.4rem; }}
  .spots li {{ margin: 0.15rem 0; }}
  .allgood {{ font-size: 1.05rem; }}
  .warnings {{ border-left: 3px solid #f0ad4e; padding-left: 0.9rem; }}
  .danger {{ border-left: 3px solid #d9534f; padding-left: 0.9rem; }}
  details {{ margin-top: 2.5rem; border-top: 1px solid #8883; padding-top: 0.8rem; }}
  details summary {{ cursor: pointer; font-size: 1.05rem; font-weight: 600; }}
  code {{ background: #8882; padding: 0.1rem 0.3rem; border-radius: 3px; }}
</style></head><body>

<h1>Revisión de {report.episode}</h1>
<p class="muted">Analizado el {report.generated_at.replace("T", " a las ")} · {stats_line}</p>

<h2>Qué tengo que hacer</h2>
{_actions_html(actions)}

<h2>Cómo ha quedado</h2>
{_summary_html(report)}

{warnings_html}

<details>
<summary>Detalle técnico</summary>

<h3>Contexto del proyecto</h3>
<p class="muted">{report.rpp_path}<br>
{report.item_count} items en «{report.es_track}» ({report.muted_items} muteados) ·
fuentes: {', '.join(report.sources) or '—'} ·
ganancia global {report.global_gain_db:+.2f} dB · envolvente de {report.envelope_points} puntos ·
FX: {', '.join(report.fx_names) or '—'} ·
tolerancias ±{report.tolerance_db:g} dB por ventana, ±{report.item_tolerance_db:g} dB por item</p>

<h3>Global (despues de los efectos)</h3>
{global_html}

<h3>¿Sigue valiendo la envolvente?</h3>
{envelope_html}

{_fx_html(report)}

<h3>Items — nivel resultante contra {report.en_track}</h3>
<p class="muted">La fuente es contexto, no un fallo: rellenar con otro audio es legitimo.
Lo que se marca es que el nivel FINAL del item se aparte de la referencia inglesa.
«env» es la ganancia media que la envolvente aplica sobre ese item.
Los tramos donde el ingles esta por debajo de {report.en_reference_floor_lufs:.1f} LUFS
quedan sin juzgar: ahi no hay referencia, solo fraseo que no coincide.</p>
<table>
<tr><th>inicio</th><th>dur</th><th>fuente</th><th>env</th><th>ganancia total</th>
<th>ES LUFS</th><th>EN LUFS</th><th>delta</th><th>pico pre-FX</th><th>nota</th></tr>
{rows_items}
</table>

<h3>Deriva por ventanas de {WINDOW_SECONDS:g}s (p{SPEECH_PERCENTILE} de LUFS-S)</h3>
<table>
<tr><th>ventana</th><th>ES</th><th>EN</th><th>delta</th></tr>
{rows_windows or "<tr><td colspan=4 class='note'>sin medida de REAPER</td></tr>"}
</table>

<h3>Puntos sueltos mas desviados</h3>
<table>
<tr><th>instante</th><th>ES LUFS-S</th><th>EN LUFS-S</th><th>delta</th></tr>
{rows_hotspots or "<tr><td colspan=4 class='note'>sin medida de REAPER</td></tr>"}
</table>

</details>
</body></html>"""
