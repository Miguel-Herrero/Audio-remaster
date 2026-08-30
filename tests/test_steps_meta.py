"""Tests de remaster/steps_meta.py.

El punto de este modulo es que la prosa no se separe del pipeline: si
alguien añade un paso, tiene que explicar que hace. Eso es justo lo que
se comprueba aqui.
"""

from __future__ import annotations

from remaster.cli import STEP_ORDER
from remaster.steps_meta import BY_KEY, STEP_INFO, step_keys


def test_step_order_comes_from_here():
    assert STEP_ORDER == step_keys()


def test_verify_sits_between_project_and_render():
    """Es donde cae de verdad: entre generar el proyecto y renderizarlo
    esta el rato en el que lo editas a mano.
    """
    assert STEP_ORDER.index("project") < STEP_ORDER.index("verify") < STEP_ORDER.index("render")


def test_every_step_is_described():
    for key in STEP_ORDER:
        info = BY_KEY[key]
        assert info.summary.strip(), key
        assert info.safeguard.strip(), key
        assert info.title.strip(), key


def test_no_extra_entries():
    assert sorted(BY_KEY) == sorted(STEP_ORDER)
    assert len(STEP_INFO) == len(STEP_ORDER)


def test_verify_is_the_only_manual_step():
    manual = [i.key for i in STEP_INFO if i.manual]
    assert manual == ["verify"]


def test_as_dict_is_json_friendly():
    d = BY_KEY["separate"].as_dict()
    assert set(d) == {"key", "title", "summary", "safeguard", "manual"}
    assert d["key"] == "separate"
    assert d["manual"] is False


def test_summaries_avoid_jargon_only_names():
    """El titulo tiene que decir algo a quien no escribio el pipeline:
    no vale repetir la clave tecnica.
    """
    for info in STEP_INFO:
        assert info.title.lower() != info.key
