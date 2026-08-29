"""Deteccion de cortes de escena por silencio en la envolvente de energia
de EN M&E. Sustituye la inspeccion manual del proyecto real: en vez de
recorrer la pista a oido buscando dónde ajustar el volumen, se localizan
los huecos de silencio suficientemente largos y se usan como fronteras.
"""

from __future__ import annotations

import numpy as np


def detect_scene_boundaries(
    envelope_db: np.ndarray,
    frame_rate: float,
    silence_threshold_db: float,
    min_duration_s: float,
) -> list[float]:
    """Punto medio de cada tramo de silencio de duracion >= min_duration_s."""
    if len(envelope_db) == 0:
        return []

    below = envelope_db < silence_threshold_db
    min_frames = max(1, int(min_duration_s * frame_rate))
    boundaries: list[float] = []

    i = 0
    n = len(below)
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            if j - i >= min_frames:
                boundaries.append(((i + j) / 2) / frame_rate)
            i = j
        else:
            i += 1
    return boundaries


def scenes_from_boundaries(boundaries: list[float], total_duration_s: float) -> list[tuple[float, float]]:
    edges = [0.0, *sorted(boundaries), total_duration_s]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]


def find_quiet_point_near(
    envelope_db: np.ndarray,
    frame_rate: float,
    target_time_s: float,
    search_window_s: float,
    silence_threshold_db: float,
    min_gap_s: float = 0.15,
) -> float | None:
    """Punto de silencio mas cercano a `target_time_s`, dentro de
    +-search_window_s, exigiendo al menos `min_gap_s` de silencio
    sostenido (suficiente para no caer en mitad de una palabra, sin ser
    tan estricto como una frontera de escena completa). None si no hay
    ningun hueco que cumpla el umbral en la ventana — quien llama decide
    el fallback (normalmente, cortar en target_time_s de todos modos).
    """
    if len(envelope_db) == 0:
        return None
    lo = max(0, int((target_time_s - search_window_s) * frame_rate))
    hi = min(len(envelope_db), int((target_time_s + search_window_s) * frame_rate))
    if hi <= lo:
        return None

    below = envelope_db[lo:hi] < silence_threshold_db
    min_frames = max(1, int(min_gap_s * frame_rate))

    best_center: float | None = None
    best_distance = float("inf")
    i, n = 0, len(below)
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            if j - i >= min_frames:
                center_time = (lo + (i + j) / 2) / frame_rate
                distance = abs(center_time - target_time_s)
                if distance < best_distance:
                    best_distance = distance
                    best_center = center_time
            i = j
        else:
            i += 1
    return best_center
