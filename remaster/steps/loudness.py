"""Paso 6: loudness global y por escena. Sustituye el procedimiento manual
de `v1.1.0.md` (comparar LUFS-I por "dry run render" y dibujar la
envolvente a mano contra el medidor LUFS-S).

La ganancia global se aplica como el FX `JS: utility/volume` de la pista
ES VOX (mismo FX que ya usas, ver reaper/rpp.py). Los ajustes por escena
son *relativos* a esa ganancia global: se escriben en la envolvente de
volumen de la pista (VOLENV2), asi que el delta que se calcula ahi ya
resta el que aporta el FX para no duplicarlo.

Seguridad de pico: igualar LUFS-I a ciegas puede empujar el pico de ES
por encima de 0dBFS si la fuente ya tenia poco margen (p.ej. los stems
separados salen normalizados a pico ~0.9 por defecto). La ganancia nunca
sube mas de lo que el pico de la señal (global o por escena) permite sin
superar PEAK_CEILING_DBFS — si hace falta, se sacrifica precision de
LUFS-I antes que distorsionar. Esto es lo que un ajuste manual a oido
hace de forma natural (bajar el gain si el medidor se pone en rojo).

Alineamiento: ES VOX no esta sincronizado 1:1 con EN VOX/EN M&E — hay un
offset (y a veces deriva) entre ambos (ver steps/align.py). Comparar
es_samples[t] con en_samples[t] directamente compara audio de instantes
distintos. Cada tramo de ES se mide en `source_time_at(align, t)`, el
mismo punto del fichero que project.py coloca para sonar en el instante
`t` de la linea de tiempo — si no, el analisis de loudness (y la propia
proteccion de pico) esta midiendo un tramo de audio que no es el que
realmente suena ahi.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..audio.envelope import energy_envelope_db, load_mono
from ..audio.lufs import LufsError, clamp_db, db_to_linear_gain, max_safe_gain_db, measure_lufs
from ..audio.scenes import detect_scene_boundaries, scenes_from_boundaries
from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult
from ..profile import LoudnessConfig
from ..reaper.rpp import VolPoint
from .align import MAX_SANE_DRIFT_SLOPE, AlignResult, source_time_at

RAMP_SECONDS = 0.15
MIN_SCENE_SECONDS_FOR_LUFS = 0.5  # pyloudnorm necesita bloques de duracion minima
# Margen bajo 0dBFS. Esta funcion solo mide el pico de la señal CRUDA
# (antes de ReaEQ) — no simula el EQ. Medido en un episodio real,
# localizando la muestra exacta que clipeaba: pico crudo -9.78dBFS + gain
# 6.00dB deberia dar -3.78dBFS, pero el render completo (gain + envolvente
# + ReaEQ + cuantizado a 16-bit) daba 0dBFS — un "impuesto" de ~3.8dB no
# explicado por la ganancia, casi seguro ReaEQ interactuando con
# contenido de alta frecuencia (una sibilante) que un techo sobre la
# señal cruda no puede ver. -6dBFS deja margen de sobra para ese efecto,
# verificado con un render real tras el cambio.
PEAK_CEILING_DBFS = -6.0


@dataclass(frozen=True)
class LoudnessResult:
    global_gain_db: float
    scene_vol_points: tuple[VolPoint, ...]


def _scene_relative_gain_db(
    es_samples: np.ndarray, en_samples: np.ndarray, sr: int,
    start_s: float, end_s: float, max_delta_db: float, global_gain_db: float,
    align: AlignResult,
) -> float | None:
    # EN nunca se desplaza: en_samples[t] es directamente el instante t.
    # ES si: el instante t de la linea de tiempo suena en
    # source_time_at(align, t) del fichero ES crudo (ver steps/align.py).
    es_i0 = int(source_time_at(align, start_s) * sr)
    es_i1 = int(source_time_at(align, end_s) * sr)
    en_i0, en_i1 = int(start_s * sr), int(end_s * sr)
    if (en_i1 - en_i0) / sr < MIN_SCENE_SECONDS_FOR_LUFS or es_i1 <= es_i0 or es_i0 < 0 or es_i1 > len(es_samples):
        return None
    es_segment = es_samples[es_i0:es_i1]
    try:
        lufs_es = measure_lufs(es_segment, sr)
        lufs_en = measure_lufs(en_samples[en_i0:en_i1], sr)
    except LufsError:
        return None
    if not (np.isfinite(lufs_es) and np.isfinite(lufs_en)):
        return None
    absolute_delta = clamp_db(lufs_en - lufs_es, max_delta_db)
    # La ganancia TOTAL en este tramo (global + relativa) no puede
    # superar lo que su propio pico local admite sin recortar.
    absolute_delta = min(absolute_delta, max_safe_gain_db(es_segment, PEAK_CEILING_DBFS))
    return absolute_delta - global_gain_db


def compute_loudness(
    es_vox_path: Path,
    en_vox_path: Path,
    en_me_path: Path,
    config: LoudnessConfig,
    align: AlignResult,
) -> LoudnessResult:
    clamped_drift = max(-MAX_SANE_DRIFT_SLOPE, min(MAX_SANE_DRIFT_SLOPE, align.drift_slope))
    align = AlignResult(align.offset_seconds, clamped_drift, align.confidence, align.low_confidence)

    es_samples, sr_es = load_mono(es_vox_path)
    en_samples, sr_en = load_mono(en_vox_path)
    me_samples, sr_me = load_mono(en_me_path)
    if not (sr_es == sr_en == sr_me):
        raise ValueError(f"Sample rates distintos: ES VOX={sr_es} EN VOX={sr_en} EN M&E={sr_me}")
    sr = sr_es

    lufs_es_global = measure_lufs(es_samples, sr)
    lufs_en_global = measure_lufs(en_samples, sr)
    lufs_matching_gain = lufs_en_global - lufs_es_global
    global_gain_db = clamp_db(min(lufs_matching_gain, max_safe_gain_db(es_samples, PEAK_CEILING_DBFS)), config.max_delta_db)

    env_db, frame_rate = energy_envelope_db(me_samples, sr, frame_ms=20.0)
    boundaries = detect_scene_boundaries(env_db, frame_rate, config.scene_min_silence_db, config.scene_min_duration_s)
    total_duration = len(me_samples) / sr
    scenes = scenes_from_boundaries(boundaries, total_duration)

    if not scenes:
        return LoudnessResult(global_gain_db, (VolPoint(0.0, 1.0), VolPoint(total_duration, 1.0)))

    gains_db = [
        _scene_relative_gain_db(es_samples, en_samples, sr, start, end, config.max_delta_db, global_gain_db, align) or 0.0
        for start, end in scenes
    ]

    points: list[VolPoint] = [VolPoint(0.0, db_to_linear_gain(gains_db[0]))]
    for i in range(1, len(scenes)):
        boundary = scenes[i][0]
        ramp_start = max(points[-1].time, boundary - RAMP_SECONDS)
        points.append(VolPoint(ramp_start, db_to_linear_gain(gains_db[i - 1])))
        points.append(VolPoint(boundary, db_to_linear_gain(gains_db[i])))
    points.append(VolPoint(total_duration, db_to_linear_gain(gains_db[-1])))

    return LoudnessResult(global_gain_db, tuple(points))


def loudness_step(
    es_vox_path: Path,
    en_vox_path: Path,
    en_me_path: Path,
    layout: EpisodeLayout,
    manifest: Manifest,
    config: LoudnessConfig,
    align: AlignResult,
    force: bool = False,
) -> tuple[LoudnessResult, StepResult]:
    cache_path = layout.state_dir / "loudness.json"
    params = {
        "max_delta_db": config.max_delta_db,
        "scene_min_silence_db": config.scene_min_silence_db,
        "scene_min_duration_s": config.scene_min_duration_s,
        # El alineamiento afecta que tramo de ES se mide para cada escena
        # (ver source_time_at): sin esto en los params, cambiar la
        # calibracion de sync no invalidaria un loudness.json ya cacheado.
        "offset_seconds": align.offset_seconds,
        "drift_slope": align.drift_slope,
        "peak_ceiling_dbfs": PEAK_CEILING_DBFS,
    }

    def fn() -> None:
        result = compute_loudness(es_vox_path, en_vox_path, en_me_path, config, align)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "global_gain_db": result.global_gain_db,
            "scene_vol_points": [
                {"time": p.time, "gain": p.gain, "shape": p.shape} for p in result.scene_vol_points
            ],
        }, indent=2))

    step_result = manifest.run_step(
        step_name="loudness",
        input_paths=[es_vox_path, en_vox_path, en_me_path],
        params=params,
        output_paths=[cache_path],
        tool_versions={},
        fn=fn,
        force=force,
    )
    data = json.loads(cache_path.read_text())
    result = LoudnessResult(
        data["global_gain_db"],
        tuple(VolPoint(p["time"], p["gain"], p["shape"]) for p in data["scene_vol_points"]),
    )
    return result, step_result
