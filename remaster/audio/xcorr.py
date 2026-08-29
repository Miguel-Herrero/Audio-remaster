"""Sync automatico ES VOX <-> EN VOX por correlacion cruzada de fase
(GCC-PHAT) sobre la envolvente de energia, con deteccion de drift lineal
mediante regresion robusta (Theil-Sen) sobre offsets locales.

Convencion de signo: `offset_seconds` es el desplazamiento que hay que
restar a la posicion de la señal `b` para alinearla con `a`, es decir
b[t] se corresponde con a[t - offset]. Un offset positivo significa que
`b` esta retrasada respecto a `a` (empieza mas tarde): para alinear el
item de `b` en REAPER hay que adelantarlo `offset` segundos (o, de forma
equivalente, recortar `offset` segundos de su inicio con SOFFS). Este
signo esta verificado con una señal sintetica en tests/test_xcorr.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import theilslopes


@dataclass(frozen=True)
class OffsetEstimate:
    offset_seconds: float
    confidence: float  # prominencia del pico, 0..1 aproximado


def gcc_phat_offset(a: np.ndarray, b: np.ndarray, frame_rate: float, max_offset_seconds: float | None = None) -> OffsetEstimate:
    if len(a) == 0 or len(b) == 0:
        return OffsetEstimate(0.0, 0.0)

    n = len(a) + len(b)
    n_fft = 1 << (n - 1).bit_length()  # siguiente potencia de 2, FFT mas rapida

    A = np.fft.rfft(a - a.mean(), n=n_fft)
    B = np.fft.rfft(b - b.mean(), n=n_fft)
    R = A * np.conj(B)
    magnitude = np.abs(R)
    magnitude[magnitude < 1e-12] = 1e-12
    R /= magnitude  # blanqueo de fase (PHAT)

    cc = np.fft.irfft(R, n=n_fft)
    # cc[k] para k pequeño = lags positivos (b adelantada), cc[-k] (indices
    # altos, "wrap around") = lags negativos (b retrasada). Reordenamos a
    # un eje de lag continuo con fftshift-like manual sobre el rango util.
    max_lag = len(a) - 1
    lags = np.arange(-max_lag, max_lag + 1)
    cc_useful = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]]) if max_lag > 0 else cc[:1]

    if max_offset_seconds is not None:
        max_lag_frames = int(max_offset_seconds * frame_rate)
        mask = np.abs(lags) <= max_lag_frames
        lags = lags[mask]
        cc_useful = cc_useful[mask]

    peak_pos = int(np.argmax(cc_useful))
    peak_lag = int(lags[peak_pos])
    peak_value = float(cc_useful[peak_pos])

    mean_abs = float(np.mean(np.abs(cc_useful))) + 1e-12
    confidence = min(1.0, max(0.0, (peak_value / mean_abs) / 20.0))

    # Verificado empiricamente (ver tests/test_xcorr.py): con esta
    # definicion de R = A * conj(B), el pico cae en -peak_lag para una
    # b retrasada. Se niega aqui para que offset_seconds>0 == "b llega
    # despues que a", tal como documenta el modulo.
    offset_seconds = -peak_lag / frame_rate
    return OffsetEstimate(offset_seconds, confidence)


@dataclass(frozen=True)
class WindowOffset:
    center_time_s: float
    offset_seconds: float
    confidence: float


@dataclass(frozen=True)
class DriftEstimate:
    global_offset: OffsetEstimate
    windows: tuple[WindowOffset, ...]
    drift_slope: float  # segundos de offset por segundo de duracion; ~0 = sin drift
    drift_intercept: float


def estimate_drift(
    env_a: np.ndarray,
    env_b: np.ndarray,
    frame_rate: float,
    n_windows: int = 20,
    window_seconds: float = 30.0,
    max_offset_seconds: float = 15.0,
) -> DriftEstimate:
    global_est = gcc_phat_offset(env_a, env_b, frame_rate, max_offset_seconds=max_offset_seconds * 4)

    total_frames = min(len(env_a), len(env_b))
    total_seconds = total_frames / frame_rate
    window_frames = max(1, int(window_seconds * frame_rate))

    windows: list[WindowOffset] = []
    if n_windows > 0 and total_seconds > window_seconds * 2:
        starts = np.linspace(0, total_frames - window_frames, num=n_windows, dtype=int)
        for start in starts:
            a_win = env_a[start: start + window_frames]
            b_win = env_b[start: start + window_frames]
            est = gcc_phat_offset(a_win, b_win, frame_rate, max_offset_seconds=max_offset_seconds)
            center_time = (start + window_frames / 2) / frame_rate
            windows.append(WindowOffset(center_time, est.offset_seconds, est.confidence))

    if len(windows) >= 3:
        good = [w for w in windows if w.confidence > 0.05] or windows
        xs = np.array([w.center_time_s for w in good])
        ys = np.array([w.offset_seconds for w in good])
        slope, intercept, *_ = theilslopes(ys, xs)
    else:
        slope, intercept = 0.0, global_est.offset_seconds

    return DriftEstimate(global_est, tuple(windows), float(slope), float(intercept))
