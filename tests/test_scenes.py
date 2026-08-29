"""Tests de deteccion de silencios (remaster/audio/scenes.py).

`find_quiet_point_near` es lo que decide DONDE cae cada corte de la
correccion de drift (ver steps/project.py): tiene que preferir un hueco
de silencio cercano a la marca nominal antes que cortar en pleno dialogo.
"""

from __future__ import annotations

import numpy as np

from remaster.audio.scenes import detect_scene_boundaries, find_quiet_point_near, scenes_from_boundaries

FRAME_RATE = 50.0  # 20ms/frame
LOUD_DB = -10.0
QUIET_DB = -50.0


def _envelope(spec: list[tuple[float, str]]) -> np.ndarray:
    """spec: lista de (duracion_s, 'loud'|'quiet') consecutivos."""
    frames = []
    for duration_s, kind in spec:
        n = int(duration_s * FRAME_RATE)
        frames.append(np.full(n, LOUD_DB if kind == "loud" else QUIET_DB))
    return np.concatenate(frames)


def test_finds_silence_gap_near_target():
    # dialogo 0-4s, silencio 4-4.5s, dialogo 4.5-10s
    env = _envelope([(4.0, "loud"), (0.5, "quiet"), (5.5, "loud")])
    point = find_quiet_point_near(env, FRAME_RATE, target_time_s=5.0, search_window_s=3.0, silence_threshold_db=-35.0)
    assert point is not None
    assert 4.0 < point < 4.5


def test_returns_none_when_no_silence_in_window():
    env = _envelope([(10.0, "loud")])
    point = find_quiet_point_near(env, FRAME_RATE, target_time_s=5.0, search_window_s=2.0, silence_threshold_db=-35.0)
    assert point is None


def test_picks_closest_gap_not_deepest():
    """Dos huecos de silencio validos dentro de la ventana: se queda con
    el mas cercano a la marca nominal, no con el mas largo.
    """
    env = _envelope([
        (2.0, "loud"), (0.3, "quiet"),   # hueco A: centrado ~2.15s
        (2.0, "loud"), (0.3, "quiet"),   # hueco B: centrado ~4.45s (mas cerca del target=4.5)
        (2.0, "loud"),
    ])
    point = find_quiet_point_near(env, FRAME_RATE, target_time_s=4.5, search_window_s=5.0, silence_threshold_db=-35.0)
    assert point is not None
    assert abs(point - 4.45) < 0.1


def test_ignores_gaps_shorter_than_min_gap():
    # un "hueco" de silencio de solo 50ms no cuenta (ruido de separacion,
    # no una pausa real de dialogo).
    env = _envelope([(4.0, "loud"), (0.05, "quiet"), (5.95, "loud")])
    point = find_quiet_point_near(
        env, FRAME_RATE, target_time_s=4.0, search_window_s=2.0,
        silence_threshold_db=-35.0, min_gap_s=0.15,
    )
    assert point is None


def test_empty_envelope_returns_none():
    assert find_quiet_point_near(np.array([]), FRAME_RATE, 5.0, 2.0, -35.0) is None


def test_detect_scene_boundaries_and_split_are_consistent():
    env = _envelope([(3.0, "loud"), (2.0, "quiet"), (3.0, "loud")])
    boundaries = detect_scene_boundaries(env, FRAME_RATE, silence_threshold_db=-35.0, min_duration_s=1.0)
    assert len(boundaries) == 1
    scenes = scenes_from_boundaries(boundaries, total_duration_s=len(env) / FRAME_RATE)
    assert len(scenes) == 2
    assert scenes[0][0] == 0.0
    assert scenes[-1][1] == len(env) / FRAME_RATE
