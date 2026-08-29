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
