"""Tests de loudness y seguridad de pico (remaster/steps/loudness.py,
remaster/audio/lufs.py).

Dos bugs reales motivan esto, los dos vistos en produccion sobre el mismo
episodio:

1. ES VOX salia de separar con Peak +0.6dBFS y Clip=14 porque el gain que
   iguala LUFS-I no comprobaba a que pico empujaba el resultado.
2. Arreglado (1), el pico SUBIO a +1.2dBFS/Clip=44 en vez de bajar: el
   analisis por escena comparaba `es_samples[t]` con `en_samples[t]`
   directamente, sin tener en cuenta el offset/drift de sincronizacion —
   media (y protegia) un tramo de ES VOX que no era el que realmente
   sonaba en ese instante de la linea de tiempo. Confirmado renderizando
   el proyecto real con REAPER (`ffmpeg -af astats`: Peak 0dB, 44 muestras
   por encima de 0dBFS, calcando el reporte del usuario) antes de
   localizar la causa.
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from remaster.audio.lufs import clamp_db, max_safe_gain_db, measure_lufs, measure_peak_dbfs
from remaster.steps.align import AlignResult
from remaster.steps.loudness import PEAK_CEILING_DBFS, _scene_relative_gain_db, compute_loudness
from remaster.profile import LoudnessConfig

SR = 48000
NO_SHIFT = AlignResult(offset_seconds=0.0, drift_slope=0.0, confidence=1.0, low_confidence=False)


def _clipping_scenario():
    t = np.linspace(0, 5.0, int(5.0 * SR), endpoint=False)
    pulse_mask = np.sin(2 * np.pi * 2 * t) > 0.9  # ~5% del tiempo en pulso alto
    es = np.where(pulse_mask, 0.95 * np.sin(2 * np.pi * 300 * t), 0.05 * np.sin(2 * np.pi * 300 * t))
    en = 0.5 * np.sin(2 * np.pi * 300 * t)
    return es.astype(np.float32), en.astype(np.float32)


# --- remaster/audio/lufs.py ---

def test_measure_peak_dbfs_matches_known_amplitude():
    samples = np.array([0.5, -0.9, 0.3])
    assert measure_peak_dbfs(samples) == pytest.approx(20 * np.log10(0.9), abs=1e-6)


def test_measure_peak_dbfs_silence_is_minus_infinity():
    assert measure_peak_dbfs(np.zeros(100)) == float("-inf")


def test_max_safe_gain_is_negative_when_already_above_ceiling():
    samples = np.full(100, 0.95)  # -0.446dBFS, por encima de un techo de -1dBFS
    assert max_safe_gain_db(samples, -1.0) < 0


def test_max_safe_gain_is_positive_when_far_below_ceiling():
    samples = np.full(100, 0.1)  # -20dBFS
    assert max_safe_gain_db(samples, -1.0) > 0


def test_max_safe_gain_is_infinite_for_silence():
    assert max_safe_gain_db(np.zeros(100), -1.0) == float("inf")


# --- caso real: ES con pico alto pero LUFS mas bajo que EN ---

def test_clipping_scenario_matching_gain_would_have_clipped():
    """Confirma que el escenario sintetico reproduce lo real: pedir
    igualar LUFS sin mirar el pico habria empujado a clipping.
    """
    es, en = _clipping_scenario()
    matching_gain = measure_lufs(en, SR) - measure_lufs(es, SR)
    naive_gain = clamp_db(matching_gain, 6.0)  # el clamp viejo, sin techo de pico
    peak_after = 20 * np.log10(np.max(np.abs(es)) * (10 ** (naive_gain / 20)))
    assert matching_gain > 0  # pedia subir
    assert peak_after > 0  # y eso se iba por encima de 0dBFS


def test_global_gain_never_exceeds_peak_ceiling(tmp_path):
    es, en = _clipping_scenario()
    es_path, en_path, me_path = tmp_path / "es.wav", tmp_path / "en.wav", tmp_path / "me.wav"
    sf.write(es_path, es, SR)
    sf.write(en_path, en, SR)
    sf.write(me_path, en, SR)  # sin huecos de silencio: sin escenas, solo se prueba el gain global

    config = LoudnessConfig(max_delta_db=6.0, scene_min_silence_db=-35.0, scene_min_duration_s=1.5)
    result = compute_loudness(es_path, en_path, me_path, config, NO_SHIFT)

    peak_after_dbfs = measure_peak_dbfs(es) + result.global_gain_db
    assert peak_after_dbfs <= PEAK_CEILING_DBFS + 1e-6
    # Y de verdad corta en vez de subir, en este escenario concreto.
    assert result.global_gain_db < 0


# --- caso real 2: la escena media el tramo equivocado si no se corrige el offset ---

def test_scene_gain_uses_the_shifted_slice_not_the_raw_one():
    """ES tiene un tramo silencioso en [0,5) y uno casi a techo en [5,10).
    Con offset=+5s, el instante de referencia t=0..4 en realidad suena con
    el tramo ES[5..9] (el peligroso) — no con ES[0..4] (el silencioso).
    Si el calculo no aplica el offset, cree que hay mucho margen (mide el
    tramo silencioso) y deja pasar una ganancia que clipea el tramo real.
    """
    n = 10 * SR
    es = np.concatenate([np.full(5 * SR, 0.02), np.full(5 * SR, 0.95)]).astype(np.float32)
    en = np.full(n, 0.5, dtype=np.float32)  # EN fuerte y constante: siempre pide subir ES

    shifted = AlignResult(offset_seconds=5.0, drift_slope=0.0, confidence=1.0, low_confidence=False)
    global_gain_db = 0.0  # aislar el efecto de la escena

    rel_gain_shifted = _scene_relative_gain_db(es, en, SR, 0.0, 4.0, 6.0, global_gain_db, shifted)
    rel_gain_unshifted = _scene_relative_gain_db(es, en, SR, 0.0, 4.0, 6.0, global_gain_db, NO_SHIFT)

    # Con el offset aplicado (correcto): mide el tramo a 0.95 -> casi sin
    # margen -> la ganancia permitida es pequeña o negativa.
    peak_after_correct = 20 * np.log10(0.95) + global_gain_db + rel_gain_shifted
    assert peak_after_correct <= PEAK_CEILING_DBFS + 1e-6

    # Sin el offset (el bug): mide el tramo a 0.02 -> mucho margen -> deja
    # pasar una ganancia grande, que aplicada al tramo real (0.95) clipea.
    peak_if_bug_were_applied_to_real_audio = 20 * np.log10(0.95) + global_gain_db + rel_gain_unshifted
    assert peak_if_bug_were_applied_to_real_audio > 0  # confirma que el bug de verdad clipeaba

    # Y la ganancia correcta es sensiblemente menor que la del calculo sin offset.
    assert rel_gain_shifted < rel_gain_unshifted - 3.0


def test_compute_loudness_end_to_end_respects_ceiling_with_offset(tmp_path):
    """Igual que el anterior pero de punta a punta por compute_loudness,
    con un hueco de silencio real en EN M&E para que se detecten escenas.
    """
    es = np.concatenate([np.full(5 * SR, 0.02), np.full(5 * SR, 0.95)]).astype(np.float32)
    en = np.concatenate([
        np.full(int(4.5 * SR), 0.5), np.zeros(int(0.6 * SR)), np.full(int(4.9 * SR), 0.5),
    ]).astype(np.float32)

    es_path, en_path, me_path = tmp_path / "es.wav", tmp_path / "en.wav", tmp_path / "me.wav"
    sf.write(es_path, es, SR)
    sf.write(en_path, en, SR)
    sf.write(me_path, en, SR)

    config = LoudnessConfig(max_delta_db=6.0, scene_min_silence_db=-35.0, scene_min_duration_s=0.3)
    shifted = AlignResult(offset_seconds=5.0, drift_slope=0.0, confidence=1.0, low_confidence=False)
    result = compute_loudness(es_path, en_path, me_path, config, shifted)

    # Simulacion completa de gain global + envolvente sobre el ES real,
    # igual que lo reproduce REAPER (interpolacion lineal entre puntos).
    times = np.array([p.time for p in result.scene_vol_points])
    gains = np.array([p.gain for p in result.scene_vol_points])
    t = np.arange(len(es)) / SR
    envelope = np.interp(t, times, gains)
    global_gain_lin = 10 ** (result.global_gain_db / 20)
    peak = np.max(np.abs(es * global_gain_lin * envelope))
    # Tolerancia holgada: redondeo acumulado por la interpolacion de la
    # envolvente, no una cuestion de exactitud del calculo en si.
    assert 20 * np.log10(peak) <= PEAK_CEILING_DBFS + 0.01
