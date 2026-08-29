"""Tests de reanudacion/determinismo (remaster/manifest.py)."""

from __future__ import annotations

from remaster.manifest import Manifest


def test_rerun_without_changes_is_a_noop(tmp_path):
    input_file = tmp_path / "in.txt"
    input_file.write_text("hola")
    output_file = tmp_path / "out.txt"

    manifest = Manifest(tmp_path / "manifest.json")
    calls = []

    def fn():
        calls.append(1)
        output_file.write_text("resultado")

    r1 = manifest.run_step("paso", [input_file], {"p": 1}, [output_file], {"tool": "1.0"}, fn)
    assert r1.ran is True
    assert len(calls) == 1

    # Segunda ejecucion: mismas entradas, mismos parametros, salida intacta.
    manifest2 = Manifest(tmp_path / "manifest.json")  # simula un proceso nuevo
    r2 = manifest2.run_step("paso", [input_file], {"p": 1}, [output_file], {"tool": "1.0"}, fn)
    assert r2.ran is False
    assert len(calls) == 1  # fn no se volvio a llamar


def test_changing_input_invalidates_the_step(tmp_path):
    input_file = tmp_path / "in.txt"
    input_file.write_text("v1")
    output_file = tmp_path / "out.txt"
    manifest = Manifest(tmp_path / "manifest.json")

    manifest.run_step("paso", [input_file], {}, [output_file], {}, lambda: output_file.write_text("r1"))

    input_file.write_text("v2 — contenido distinto")
    calls = []
    manifest.run_step("paso", [input_file], {}, [output_file], {},
                       lambda: (calls.append(1), output_file.write_text("r2")))
    assert calls == [1]


def test_changing_params_invalidates_the_step(tmp_path):
    input_file = tmp_path / "in.txt"
    input_file.write_text("igual")
    output_file = tmp_path / "out.txt"
    manifest = Manifest(tmp_path / "manifest.json")

    manifest.run_step("paso", [input_file], {"ratio": "0.95904"}, [output_file], {},
                       lambda: output_file.write_text("r1"))

    calls = []
    manifest.run_step("paso", [input_file], {"ratio": "0.99999"}, [output_file], {},
                       lambda: (calls.append(1), output_file.write_text("r2")))
    assert calls == [1]


def test_force_reruns_even_without_changes(tmp_path):
    input_file = tmp_path / "in.txt"
    input_file.write_text("igual")
    output_file = tmp_path / "out.txt"
    manifest = Manifest(tmp_path / "manifest.json")

    manifest.run_step("paso", [input_file], {}, [output_file], {}, lambda: output_file.write_text("r1"))

    calls = []
    manifest.run_step("paso", [input_file], {}, [output_file], {},
                       lambda: (calls.append(1), output_file.write_text("r2")), force=True)
    assert calls == [1]


def test_deleted_output_invalidates_the_step(tmp_path):
    input_file = tmp_path / "in.txt"
    input_file.write_text("igual")
    output_file = tmp_path / "out.txt"
    manifest = Manifest(tmp_path / "manifest.json")

    manifest.run_step("paso", [input_file], {}, [output_file], {}, lambda: output_file.write_text("r1"))
    output_file.unlink()

    calls = []
    manifest.run_step("paso", [input_file], {}, [output_file], {},
                       lambda: (calls.append(1), output_file.write_text("r2")))
    assert calls == [1]
