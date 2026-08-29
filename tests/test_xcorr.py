"""Tests de sync automatico (remaster/audio/xcorr.py).

Los numeros de la convencion de signo se verificaron primero con un
script ad hoc antes de escribir esto (offset_seconds>0 significa que la
segunda señal llega despues que la primera).
"""

from __future__ import annotations

import numpy as np

from remaster.audio.xcorr import estimate_drift, gcc_phat_offset

FRAME_RATE = 50.0  # 20ms/frame


def _structured_noise(n: int, seed: int = 42) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=n)


def test_recovers_known_positive_delay():
    a = _structured_noise(400)
    delay_frames = int(2.0 * FRAME_RATE)
    b = np.concatenate([np.zeros(delay_frames), a])[: len(a)]

    est = gcc_phat_offset(a, b, FRAME_RATE)

    assert abs(est.offset_seconds - 2.0) < 1.0 / FRAME_RATE
    assert est.confidence > 0.5


def test_recovers_known_negative_delay():
    a = _structured_noise(400)
    advance_frames = int(1.5 * FRAME_RATE)
    b = np.concatenate([a[advance_frames:], np.zeros(advance_frames)])

    est = gcc_phat_offset(a, b, FRAME_RATE)

    assert abs(est.offset_seconds - (-1.5)) < 1.0 / FRAME_RATE


def test_zero_delay_gives_zero_offset():
    a = _structured_noise(400)
    est = gcc_phat_offset(a, a.copy(), FRAME_RATE)
    assert abs(est.offset_seconds) < 1.0 / FRAME_RATE
    assert est.confidence > 0.9


def test_drift_estimation_recovers_linear_ratio_error():
    """Simula un audio retimeado con un ratio ligeramente incorrecto:
    el offset entre las dos señales crece de forma lineal con el tiempo.
    """
    a = _structured_noise(3000, seed=7)  # 60s
    t = np.arange(len(a))
    drift_rate = 0.01  # 1% de retardo acumulado
    idx = np.clip((t - t * drift_rate).astype(int), 0, len(a) - 1)
    b = a[idx]

    drift = estimate_drift(a, b, FRAME_RATE, n_windows=10, window_seconds=5.0, max_offset_seconds=5.0)

    assert len(drift.windows) == 10
    # El signo del ratio real (b se retrasa mas cuanto mas avanza el tiempo)
    # debe verse como una pendiente positiva del offset con el tiempo.
    assert drift.drift_slope > 0.003


def test_flat_signal_has_low_confidence():
    """Sin estructura (silencio total) no hay pico claro que fiarse."""
    a = np.zeros(400)
    b = np.zeros(400)
    est = gcc_phat_offset(a, b, FRAME_RATE)
    assert est.confidence < 0.2
