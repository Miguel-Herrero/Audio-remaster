"""Envolvente de energia de una señal de audio, usada tanto por el sync
(xcorr.py) como por la deteccion de escenas (scenes.py).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def load_mono(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=False, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


def peak_frames(data: np.ndarray, frame_len: int, chunk: int = 4096) -> np.ndarray:
    """Pico absoluto (lineal) por bloque de `frame_len` muestras, tomando el
    maximo sobre TODOS los canales.

    Se calcula por trozos en vez de sobre el array entero porque `np.abs`
    de un episodio completo en estereo duplica ~1.2 GB en memoria sin
    necesidad.
    """
    if data.ndim == 1:
        data = data[:, None]
    n_frames = len(data) // frame_len
    tail = len(data) - n_frames * frame_len
    peaks = np.empty(n_frames + (1 if tail else 0), dtype=np.float64)
    n_channels = data.shape[1]
    for start in range(0, n_frames, chunk):
        stop = min(start + chunk, n_frames)
        block = data[start * frame_len: stop * frame_len]
        peaks[start:stop] = np.abs(block).reshape(stop - start, frame_len, n_channels).max(axis=(1, 2))
    if tail:
        peaks[-1] = np.abs(data[n_frames * frame_len:]).max()
    return peaks


def load_mono_and_peak_frames(
    path: Path, frame_ms: float = 20.0
) -> tuple[np.ndarray, int, np.ndarray, float]:
    """`load_mono` + el pico REAL por frame, antes de mezclar a mono.

    Existe porque el downmix a mono de `load_mono` esconde el pico: un
    transitorio presente en un solo canal se divide por 2 al promediar
    (-6 dB en el peor caso). Medido en `01_stems/es_vocals.flac` de
    s03e01: pico real -2.6 dBFS (canal derecho), pico del mono -5.2 dBFS
    — 2.6 dB de error, siempre en la direccion optimista. Ese error
    llegaba entero a `max_safe_gain_db` y de ahi a la ganancia global,
    que es exactamente la que tenia que impedir el recorte.

    Devuelve (mono, sample_rate, picos_por_frame, frames_por_segundo).
    """
    data, sr = sf.read(str(path), always_2d=True, dtype="float32")
    frame_len = max(1, int(sr * frame_ms / 1000))
    peaks = peak_frames(data, frame_len)
    mono = data.mean(axis=1)
    del data
    return mono, sr, peaks, sr / frame_len


def energy_envelope_db(samples: np.ndarray, sample_rate: int, frame_ms: float = 20.0) -> tuple[np.ndarray, float]:
    """RMS por ventanas de `frame_ms`, en dBFS. Devuelve (envolvente, frame_rate_hz)."""
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return np.array([]), 1000.0 / frame_ms
    trimmed = samples[: n_frames * frame_len].reshape(n_frames, frame_len).astype(np.float64)
    rms = np.sqrt(np.mean(trimmed ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    frame_rate = 1000.0 / frame_ms
    return db, frame_rate
