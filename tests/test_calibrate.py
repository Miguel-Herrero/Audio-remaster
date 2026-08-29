"""Tests de calibracion manual por N puntos (remaster/steps/align.py).

Los valores del caso de 2 puntos son los medidos a mano en REAPER para
s03e01 (ver conversacion): reproducen offset=-0.844s, drift=-0.00073s/s,
que ya se verifico por separado contra las dos medidas reales.
"""

from __future__ import annotations

import pytest

from remaster.steps.align import CalibrationPoint, calibrate_from_points


def test_two_points_matches_hand_derived_real_case():
    points = [
        CalibrationPoint(es_time=45.456, en_time=47.414),
        CalibrationPoint(es_time=2179.492, en_time=2183.011),
    ]
    result = calibrate_from_points(points, against_offset=1.08)

    assert result.offset_seconds == pytest.approx(-0.844, abs=0.001)
    assert result.drift_slope == pytest.approx(-0.0007309, abs=1e-6)
    # Con 2 puntos el ajuste es exacto: residuo ~0 en ambos.
    assert all(abs(r) < 1e-6 for r in result.residuals)


def test_two_points_no_drift():
    """Dos puntos con el mismo offset (sin deriva real) -> drift ~0."""
    points = [
        CalibrationPoint(es_time=10.0, en_time=11.0),
        CalibrationPoint(es_time=110.0, en_time=111.0),
    ]
    result = calibrate_from_points(points)

    assert result.offset_seconds == pytest.approx(-1.0, abs=1e-9)
    assert result.drift_slope == pytest.approx(0.0, abs=1e-9)


def test_three_points_least_squares_reduces_outlier_influence():
    """3 puntos, uno con un pequeño error de medida: el ajuste no debe
    pasar exactamente por los 3 (imposible con una recta), pero debe
    quedar mas cerca de los dos consistentes que un ajuste con solo esos
    dos ignorando el tercero.
    """
    consistent = [
        CalibrationPoint(es_time=10.0, en_time=11.0),
        CalibrationPoint(es_time=210.0, en_time=211.0),
    ]
    with_outlier = consistent + [CalibrationPoint(es_time=110.0, en_time=110.5)]  # 0.5s de error de medida

    exact_fit = calibrate_from_points(consistent)
    least_squares_fit = calibrate_from_points(with_outlier)

    # El resultado con el punto ruidoso se desvia del ajuste "limpio",
    # pero no de forma disparatada (el ajuste absorbe el error, no lo sigue a ciegas).
    assert least_squares_fit.offset_seconds != pytest.approx(exact_fit.offset_seconds, abs=1e-6)
    assert abs(least_squares_fit.offset_seconds - exact_fit.offset_seconds) < 0.5
    # Los residuos ya no son todos ~0: el ajuste no pasa exacto por los 3 puntos.
    assert max(abs(r) for r in least_squares_fit.residuals) > 0.01


def test_requires_at_least_two_points():
    with pytest.raises(ValueError):
        calibrate_from_points([CalibrationPoint(es_time=1.0, en_time=2.0)])


def test_against_offset_is_undone_before_fitting():
    """Si se midio contra un proyecto con un offset constante ya
    aplicado, against_offset lo deshace antes de ajustar. EN nunca se
    toca (nunca se desplaza), asi que solo ES necesita la correccion.
    """
    raw_points = [
        CalibrationPoint(es_time=10.0, en_time=11.0),
        CalibrationPoint(es_time=210.0, en_time=211.0),
    ]
    baseline = calibrate_from_points(raw_points)

    shifted_points = [CalibrationPoint(es_time=p.es_time - 2.0, en_time=p.en_time) for p in raw_points]
    corrected = calibrate_from_points(shifted_points, against_offset=2.0)

    assert corrected.offset_seconds == pytest.approx(baseline.offset_seconds, abs=1e-9)
    assert corrected.drift_slope == pytest.approx(baseline.drift_slope, abs=1e-9)
