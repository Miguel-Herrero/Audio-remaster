"""Paso 5: sync automatico ES VOX <-> EN VOX. Sustituye el "alineado por
onda a ojo" de las notas SH1984 por GCC-PHAT + deteccion de drift lineal
(ver remaster/audio/xcorr.py para la convencion de signo).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..audio.envelope import energy_envelope_db, load_mono
from ..audio.xcorr import DriftEstimate, estimate_drift
from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult

CONFIDENCE_WARN_THRESHOLD = 0.3
FRAME_MS = 20.0
N_WINDOWS = 20
WINDOW_SECONDS = 30.0
MAX_OFFSET_SECONDS = 15.0
# Deriva maxima que se acepta sin rechistar en cualquier consumidor de un
# AlignResult (project.py al colocar items, loudness.py al medir que
# tramo de ES suena en cada instante). Una estimacion mayor casi seguro
# es ruido del algoritmo automatico, no un desajuste real — ver
# remaster/audio/xcorr.py y la conversacion que motivo este limite.
MAX_SANE_DRIFT_SLOPE = 0.005


class AlignError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlignResult:
    offset_seconds: float
    drift_slope: float
    confidence: float
    low_confidence: bool


def source_time_at(align: AlignResult, reference_time: float) -> float:
    """El instante del ES crudo (sin recortar/desplazar) que suena en el
    instante `reference_time` de la linea de tiempo (tiempo EN, que nunca
    se desplaza) — misma formula que usa project.py para colocar los
    items. Cualquier analisis que compare ES contra EN en un instante
    concreto (loudness por escena, etc.) tiene que pasar por aqui: comparar
    es_samples[t] con en_samples[t] directamente esta comparando audio
    desalineado en cuanto offset o drift no son ~0.
    """
    return reference_time + align.offset_seconds + align.drift_slope * reference_time


@dataclass(frozen=True)
class CalibrationPoint:
    es_time: float
    en_time: float


@dataclass(frozen=True)
class CalibrationResult:
    offset_seconds: float
    drift_slope: float
    residuals: tuple[float, ...]  # es_time predicho - es_time medido, por punto


def _tool_versions() -> dict[str, str]:
    import numpy
    import scipy
    return {"numpy": numpy.__version__, "scipy": scipy.__version__}


def calibrate_from_points(points: list[CalibrationPoint], against_offset: float = 0.0) -> CalibrationResult:
    """Offset y drift a partir de N>=2 puntos (es_time, en_time) medidos a
    oido — el mismo calculo que se hizo a mano para el primer episodio,
    generalizado. Con 2 puntos es la solucion exacta; con mas, ajuste por
    minimos cuadrados (`numpy.polyfit` resuelve ambos casos igual).

    Los tiempos deben medirse contra fuentes SIN corregir (offset=0,
    drift=0) — p.ej. reproduciendo es_vocals.flac/en_vocals.flac por
    separado, o un proyecto generado con --sync-offset 0 --sync-drift 0.
    Si se midio contra un proyecto que ya tenia un offset CONSTANTE
    aplicado (sin drift), pasa ese valor en `against_offset` y se
    deshace antes de ajustar — EN nunca se desplaza, asi que sus tiempos
    no necesitan correccion.

    Convencion (ver remaster/audio/xcorr.py): el tiempo en la fuente ES
    para un instante de referencia (EN) T es es(T) = T*(1+drift) + offset.
    Es una recta: se ajusta con regresion lineal es = a + b*en, y
    offset=a, drift=b-1.
    """
    if len(points) < 2:
        raise ValueError("Hacen falta al menos 2 puntos para calibrar")

    import numpy as np

    en_times = np.array([p.en_time for p in points], dtype=float)
    raw_es_times = np.array([p.es_time + against_offset for p in points], dtype=float)

    b, a = np.polyfit(en_times, raw_es_times, deg=1)
    predicted = a + b * en_times
    residuals = tuple((predicted - raw_es_times).tolist())

    return CalibrationResult(offset_seconds=float(a), drift_slope=float(b - 1.0), residuals=residuals)


def manual_align(
    offset_seconds: float,
    drift_slope: float,
    layout: EpisodeLayout,
    manifest: Manifest,
    force: bool = False,
) -> tuple[AlignResult, StepResult]:
    """Calibracion manual: para cuando GCC-PHAT no da una lectura fiable
    (confianza baja y sin patron claro en varias ventanas — ver
    .remaster/align.png) pero el usuario puede medir a oido dos puntos
    limpios en REAPER. offset_seconds/drift_slope sustituyen por completo
    al resultado automatico y quedan grabados en align.json igual que si
    se hubieran calculado, para que el resto del pipeline (project, notes)
    no note la diferencia y quede reproducible.
    """
    params = {"manual": True, "offset_seconds": offset_seconds, "drift_slope": drift_slope}

    def fn() -> None:
        layout.align_json_path.parent.mkdir(parents=True, exist_ok=True)
        layout.align_json_path.write_text(json.dumps({
            "offset_seconds": offset_seconds,
            "drift_slope": drift_slope,
            "confidence": 1.0,
            "low_confidence": False,
            "manual_override": True,
            "windows": [],
        }, indent=2))
        # Sin PNG de diagnostico automatico: no hay ventanas que graficar.
        if layout.align_png_path.exists():
            layout.align_png_path.unlink()

    step_result = manifest.run_step(
        step_name="align",
        input_paths=[],
        params=params,
        output_paths=[layout.align_json_path],
        tool_versions={},
        fn=fn,
        force=force,
    )
    data = json.loads(layout.align_json_path.read_text())
    result = AlignResult(data["offset_seconds"], data["drift_slope"], data["confidence"], data["low_confidence"])
    return result, step_result


def _write_diagnostic_png(drift: DriftEstimate, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    if drift.windows:
        xs = [w.center_time_s for w in drift.windows]
        ys = [w.offset_seconds for w in drift.windows]
        conf = [w.confidence for w in drift.windows]
        sc = ax.scatter(xs, ys, c=conf, cmap="viridis", vmin=0, vmax=1, label="ventanas")
        fig.colorbar(sc, label="confianza")
        fit_xs = [min(xs), max(xs)]
        fit_ys = [drift.drift_slope * x + drift.drift_intercept for x in fit_xs]
        ax.plot(fit_xs, fit_ys, "r--", label=f"drift {drift.drift_slope:.5f} s/s")
    ax.axhline(
        drift.global_offset.offset_seconds, color="gray", linestyle=":",
        label=f"offset global {drift.global_offset.offset_seconds:.3f}s",
    )
    ax.set_xlabel("tiempo (s)")
    ax.set_ylabel("offset ES vs EN (s)")
    ax.set_title("Sync automatico ES VOX / EN VOX")
    ax.legend(loc="best", fontsize=8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def run_align(
    es_vox_path: Path,
    en_vox_path: Path,
    layout: EpisodeLayout,
    manifest: Manifest,
    force: bool = False,
) -> tuple[AlignResult, StepResult]:
    params = {
        "frame_ms": FRAME_MS, "n_windows": N_WINDOWS,
        "window_seconds": WINDOW_SECONDS, "max_offset_seconds": MAX_OFFSET_SECONDS,
    }

    def fn() -> None:
        en_samples, sr_en = load_mono(en_vox_path)
        es_samples, sr_es = load_mono(es_vox_path)
        if sr_en != sr_es:
            raise AlignError(f"Sample rates distintos: EN VOX={sr_en}Hz, ES VOX={sr_es}Hz")

        env_en, frame_rate = energy_envelope_db(en_samples, sr_en, FRAME_MS)
        env_es, _ = energy_envelope_db(es_samples, sr_es, FRAME_MS)
        drift = estimate_drift(
            env_en, env_es, frame_rate,
            n_windows=N_WINDOWS, window_seconds=WINDOW_SECONDS, max_offset_seconds=MAX_OFFSET_SECONDS,
        )

        result = AlignResult(
            offset_seconds=drift.global_offset.offset_seconds,
            drift_slope=drift.drift_slope,
            confidence=drift.global_offset.confidence,
            low_confidence=drift.global_offset.confidence < CONFIDENCE_WARN_THRESHOLD,
        )
        layout.align_json_path.parent.mkdir(parents=True, exist_ok=True)
        layout.align_json_path.write_text(json.dumps({
            "offset_seconds": result.offset_seconds,
            "drift_slope": result.drift_slope,
            "confidence": result.confidence,
            "low_confidence": result.low_confidence,
            "windows": [asdict(w) for w in drift.windows],
        }, indent=2))
        _write_diagnostic_png(drift, layout.align_png_path)

    step_result = manifest.run_step(
        step_name="align",
        input_paths=[en_vox_path, es_vox_path],
        params=params,
        output_paths=[layout.align_json_path, layout.align_png_path],
        tool_versions=_tool_versions(),
        fn=fn,
        force=force,
    )

    data = json.loads(layout.align_json_path.read_text())
    result = AlignResult(data["offset_seconds"], data["drift_slope"], data["confidence"], data["low_confidence"])
    return result, step_result
