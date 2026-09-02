"""Estimacion de cuanto va a tardar una separacion."""

import json

import pytest

from remaster.msst import MsstModel
from remaster.msst_timing import (
    FALLBACK,
    MAX_SAMPLES,
    Speed,
    device_for,
    fit,
    format_eta,
    key_for,
    load_samples,
    record,
    speed_for,
)


@pytest.fixture
def model():
    return MsstModel(
        id="demo", name="Demo", model_type="bs_roformer",
        config="repo:c.yaml", checkpoint="data:p.ckpt", stems=("vocals",),
        speed=(("mps", 4.0, 0.5), ("cpu", 4.0, 3.0)),
    )


# --------------------------------------------------------------------------
# La recta
# --------------------------------------------------------------------------

def test_the_estimate_has_a_fixed_part_and_a_variable_one():
    """Cargar el modelo cuesta lo mismo con un audio de 3 s que con uno de
    una hora; solo la segunda parte crece.
    """
    speed = Speed(load_s=10.0, rate=2.0)
    assert speed.estimate(0) == 10.0
    assert speed.estimate(60) == 130.0


def test_a_negative_duration_does_not_go_backwards():
    assert Speed(load_s=5.0, rate=2.0).estimate(-30) == 5.0


# --------------------------------------------------------------------------
# Aprender de las ejecuciones
# --------------------------------------------------------------------------

def test_two_different_lengths_recover_the_line():
    """Con dos puntos se puede separar carga de ritmo: 10 s -> 30 s y
    30 s -> 70 s es una carga de 10 y un ritmo de 2.
    """
    speed = fit([[10, 30], [30, 70]], FALLBACK["cpu"])
    assert speed.rate == pytest.approx(2.0)
    assert speed.load_s == pytest.approx(10.0)


def test_a_single_length_keeps_the_known_rate(model):
    """Un solo tamano de audio no da para inventar una pendiente."""
    known = Speed(load_s=0.0, rate=2.0)
    speed = fit([[10, 30]], known)
    assert speed.rate == 2.0
    assert speed.load_s == pytest.approx(10.0)


def test_noisy_samples_do_not_produce_a_negative_rate():
    """Mas audio no puede tardar menos: si sale eso, es ruido y se descarta."""
    assert fit([[10, 100], [60, 20]], FALLBACK["cpu"]) is None


def test_samples_that_are_too_short_are_ignored():
    """Un fragmento de dos segundos es casi todo carga: mediria cualquier cosa."""
    assert fit([[1, 8], [2, 9]], FALLBACK["cpu"]) is None


def test_recording_and_reading_back(tmp_path):
    path = tmp_path / "timings.json"
    record(path, "demo@cpu", 30.0, 95.0)
    assert load_samples(path)["demo@cpu"] == [[30.0, 95.0]]


def test_a_too_short_run_is_not_recorded(tmp_path):
    path = tmp_path / "timings.json"
    record(path, "demo@cpu", 2.0, 9.0)
    assert load_samples(path) == {}


def test_only_the_last_samples_are_kept(tmp_path):
    """Cambiar de maquina no debe quedar enterrado bajo el historial viejo."""
    path = tmp_path / "timings.json"
    for i in range(MAX_SAMPLES + 5):
        record(path, "demo@cpu", 30.0, 90.0 + i)
    entries = load_samples(path)["demo@cpu"]
    assert len(entries) == MAX_SAMPLES
    assert entries[-1] == [30.0, 90.0 + MAX_SAMPLES + 4]


def test_a_corrupt_history_is_not_fatal(tmp_path):
    path = tmp_path / "timings.json"
    path.write_text("{ esto no es json")
    assert load_samples(path) == {}


def test_an_unwritable_history_does_not_raise(tmp_path):
    """Estimar mejor es una comodidad; separar es lo importante."""
    path = tmp_path / "archivo" / "timings.json"
    path.parent.write_text("soy un fichero, no una carpeta")
    record(path, "demo@cpu", 30.0, 90.0)  # no debe lanzar


# --------------------------------------------------------------------------
# De donde sale la cifra
# --------------------------------------------------------------------------

def test_without_history_the_catalog_speed_is_used(model, tmp_path):
    speed, source = speed_for(model, None, "mps", tmp_path / "vacio.json")
    assert speed.rate == 0.5
    assert "fabrica" in source


def test_with_history_the_measurements_win(model, tmp_path):
    path = tmp_path / "timings.json"
    path.write_text(json.dumps({"demo@mps": [[10, 30], [30, 70]]}))
    speed, source = speed_for(model, None, "mps", path)
    assert speed.rate == pytest.approx(2.0)
    assert "tuyas" in source


def test_history_is_kept_per_device(model, tmp_path):
    """Lo que tarda en CPU no dice nada de lo que tardara en la GPU."""
    path = tmp_path / "timings.json"
    path.write_text(json.dumps({"demo@cpu": [[10, 300], [30, 900]]}))
    speed, source = speed_for(model, None, "mps", path)
    assert speed.rate == 0.5
    assert "fabrica" in source


def test_history_is_kept_per_language(model, tmp_path):
    assert key_for("bandit_v2", "spa", "cpu") == "bandit_v2/spa@cpu"
    assert key_for("bandit_v2", None, "cpu") == "bandit_v2@cpu"


def test_a_model_without_catalog_speed_falls_back_to_the_generic_one(tmp_path):
    bare = MsstModel(id="x", name="X", model_type="t", config="repo:c",
                     checkpoint="data:p", stems=("vocals",))
    speed, _ = speed_for(bare, None, "cpu", tmp_path / "vacio.json")
    assert speed == FALLBACK["cpu"]


# --------------------------------------------------------------------------
# El dispositivo
# --------------------------------------------------------------------------

def test_a_cpu_only_model_is_estimated_on_cpu(model):
    """Bandit acaba en CPU aunque haya GPU: estimarlo en MPS mentiria."""
    cpu_bound = MsstModel(**{**model.__dict__, "cpu_only": True})
    assert device_for(cpu_bound) == "cpu"


def test_forcing_cpu_is_respected(model):
    assert device_for(model, force_cpu=True) == "cpu"


# --------------------------------------------------------------------------
# Como se dice
# --------------------------------------------------------------------------

def test_short_runs_do_not_pretend_to_be_precise():
    assert format_eta(20) == "menos de un minuto"


def test_minutes_are_rounded():
    assert format_eta(305) == "~5 min"


def test_long_runs_are_rounded_to_five_minutes():
    """Decir "~37 min" finge una precision que no hay."""
    assert format_eta(37 * 60) == "~35 min"


def test_very_long_runs_switch_to_hours():
    assert format_eta(90 * 60) == "~1.5 h"
    assert format_eta(120 * 60) == "~2 h"
