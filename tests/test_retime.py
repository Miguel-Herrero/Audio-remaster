"""Tests del calculo de ratio de retime (remaster/steps/retime.py)."""

from __future__ import annotations

from fractions import Fraction

import pytest

from remaster.steps.retime import RetimeError, _atempo_arg, compute_atempo_ratio


def test_dvd_pal_to_bluray_ratio_matches_known_value():
    """25 fps -> 24000/1001 fps. Este es el caso real del proyecto: el
    valor 0.95904 esta en todas las notas SH1984 y en el atempo que ya
    se ha usado para producir los ficheros existentes en ~/Movies.
    """
    ratio = compute_atempo_ratio(Fraction(25, 1), Fraction(24000, 1001))
    assert _atempo_arg(ratio) == "0.95904"


def test_ratio_is_identity_when_fps_match():
    ratio = compute_atempo_ratio(Fraction(24000, 1001), Fraction(24000, 1001))
    assert ratio == 1


def test_other_fps_pairs_are_supported():
    # 30 -> 24000/1001 (NTSC a cine): ratio ~0.7992
    ratio = compute_atempo_ratio(Fraction(30, 1), Fraction(24000, 1001))
    assert abs(float(ratio) - 0.79920) < 1e-5


def test_zero_source_fps_is_rejected():
    with pytest.raises(RetimeError):
        compute_atempo_ratio(Fraction(0, 1), Fraction(24000, 1001))
