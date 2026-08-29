"""Medicion de loudness (LUFS, BS.1770) con pyloudnorm."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyloudnorm as pyln


class LufsError(RuntimeError):
    pass


def measure_lufs(samples: np.ndarray, sample_rate: int) -> float:
    """LUFS integrado de un array (mono o estereo, forma (n,) o (n, ch))."""
    meter = pyln.Meter(sample_rate)
    try:
        return float(meter.integrated_loudness(samples))
    except ValueError as exc:
        raise LufsError(f"No se pudo medir LUFS (¿segmento demasiado corto o silencioso?): {exc}") from exc


def db_to_linear_gain(db: float) -> float:
    return float(10 ** (db / 20))


def clamp_db(value_db: float, max_abs_db: float) -> float:
    return float(np.clip(value_db, -max_abs_db, max_abs_db))


def measure_peak_dbfs(samples: np.ndarray) -> float:
    """Pico de muestra (no true-peak) en dBFS. -inf si la señal es
    silencio total (sin pico que proteger).
    """
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if peak <= 0:
        return float("-inf")
    return 20 * np.log10(peak)


def max_safe_gain_db(samples: np.ndarray, ceiling_dbfs: float) -> float:
    """Cuanto se puede subir `samples` (en dB) sin que su pico supere
    `ceiling_dbfs`. Puede salir negativo si la señal YA supera el techo
    sin ganancia ninguna (entonces hay que atenuar, no solo no subir).
    +inf si la señal es silencio total.
    """
    peak_db = measure_peak_dbfs(samples)
    if not np.isfinite(peak_db):
        return float("inf")
    return ceiling_dbfs - peak_db
