"""El pico se medía sobre el downmix a mono y por eso salía optimista.

Bug real, medido sobre `01_stems/es_vocals.flac` de s03e01: pico real
-2.6 dBFS (canal derecho), pico del mono -5.2 dBFS. Los 2.6 dB de error
llegaban intactos a `max_safe_gain_db` y de ahi a la ganancia global, que
es justo la que tenia que impedir el recorte. Con el techo en -6 dBFS la
ganancia salia -0.8 dB y el render aterrizaba en -0.8 dBFS: 0.8 dB de
margen donde el codigo creia tener 6.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf

from remaster.audio.envelope import load_mono, load_mono_and_peak_frames, peak_frames
from remaster.audio.lufs import max_safe_gain_db, max_safe_gain_db_from_peaks, measure_peak_dbfs

SR = 48000


def _one_sided_peak_signal(seconds: float = 1.0) -> np.ndarray:
    """Estereo con un transitorio fuerte SOLO en el canal derecho. Es la
    forma del problema: al promediar canales, ese pico se divide por 2.
    """
    n = int(SR * seconds)
    left = np.full(n, 0.1, dtype=np.float32)
    right = np.full(n, 0.1, dtype=np.float32)
    right[n // 2] = 0.9
    return np.stack([left, right], axis=1)


def test_mono_downmix_hides_a_one_sided_peak(tmp_path):
    path = tmp_path / "one_sided.wav"
    sf.write(path, _one_sided_peak_signal(), SR)

    mono, _ = load_mono(path)
    _, _, peaks, _ = load_mono_and_peak_frames(path)

    mono_peak_db = measure_peak_dbfs(mono)
    true_peak_db = 20 * np.log10(float(np.max(peaks)))

    assert true_peak_db > mono_peak_db + 5.0  # el mono lo esconde ~6 dB
    # holgura de 1e-3: el WAV se escribe en PCM 16-bit y cuantiza el 0.9
    assert abs(true_peak_db - 20 * np.log10(0.9)) < 1e-3


def test_gain_from_mono_would_overshoot_the_ceiling(tmp_path):
    """El fallo en una linea: la ganancia calculada sobre el mono deja el
    pico REAL por encima del techo.
    """
    path = tmp_path / "one_sided.wav"
    sf.write(path, _one_sided_peak_signal(), SR)
    ceiling = -6.0

    mono, _ = load_mono(path)
    _, _, peaks, _ = load_mono_and_peak_frames(path)

    gain_from_mono = max_safe_gain_db(mono, ceiling)
    gain_from_channels = max_safe_gain_db_from_peaks(peaks, ceiling)

    true_peak_db = 20 * np.log10(float(np.max(peaks)))
    assert true_peak_db + gain_from_mono > ceiling  # el metodo viejo se pasa
    assert true_peak_db + gain_from_channels <= ceiling + 1e-6  # el nuevo no


def test_peak_frames_cover_the_whole_signal_including_the_tail():
    """Un pico en la cola incompleta (que no llena un frame entero) no se
    puede perder: seria justo el que recorta.
    """
    frame_len = 1000
    data = np.zeros((frame_len * 3 + 37, 2), dtype=np.float32)
    data[-1, 0] = 0.8
    peaks = peak_frames(data, frame_len)
    assert len(peaks) == 4
    assert float(np.max(peaks)) == np.float32(0.8)


def test_peak_frames_slice_maps_to_time():
    """La proteccion por escena corta el array de picos por indice de
    frame: el mapeo tiene que ser el mismo que usa loudness.py.
    """
    frame_len = SR // 50  # 20 ms
    n = SR * 2
    data = np.zeros((n, 1), dtype=np.float32)
    data[int(1.5 * SR), 0] = 0.7  # pico a los 1.5 s
    peaks = peak_frames(data, frame_len)
    rate = SR / frame_len

    quiet = peaks[: int(1.0 * rate)]
    loud = peaks[int(1.4 * rate): int(1.6 * rate)]
    assert float(np.max(quiet)) == 0.0
    assert float(np.max(loud)) == np.float32(0.7)
