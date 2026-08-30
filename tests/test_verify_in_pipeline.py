"""`verify` como paso del pipeline (remaster/cli.py:_run_verify_in_pipeline).

La regla que se comprueba aqui es que NUNCA corta el run: devuelve un
motivo para que el paso salga como omitido, en vez de lanzar y dejarte sin
render. Y que sin medida de REAPER se omite en vez de dar un analisis a
medias sin decirlo.
"""

from __future__ import annotations

from remaster import cli
from remaster.layout import EpisodeLayout
from remaster.profile import load_profile


def _layout(tmp_path):
    layout = EpisodeLayout(root=tmp_path / "s01e01")
    layout.ensure_dirs()
    return layout


def test_skips_when_there_is_no_project(tmp_path):
    reason = cli._run_verify_in_pipeline(_layout(tmp_path), load_profile())
    assert reason is not None
    assert "proyecto" in reason


def test_skips_when_there_is_no_reaper_measurement(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    layout.rpp_edit.write_text("<REAPER_PROJECT 0.1 '7.0'\n>\n")
    monkeypatch.setattr(cli, "newest_render_stats", lambda dirs: None)

    reason = cli._run_verify_in_pipeline(layout, load_profile())
    assert reason is not None
    assert "medida de REAPER" in reason
    # El motivo tiene que decir que hacer, no solo que falta algo.
    assert "dry run render" in reason


def test_a_verify_error_becomes_a_reason_not_an_exception(tmp_path, monkeypatch):
    """Un .RPP sin las pistas esperadas omite el paso; no revienta el run."""
    layout = _layout(tmp_path)
    layout.rpp_edit.write_text("<REAPER_PROJECT 0.1 '7.0'\n>\n")
    stats = tmp_path / "render_stats.html"
    stats.write_text("<html></html>")
    monkeypatch.setattr(cli, "newest_render_stats", lambda dirs: stats)

    reason = cli._run_verify_in_pipeline(layout, load_profile())
    assert reason is not None


def test_verify_never_reports_success_without_a_report(tmp_path, monkeypatch):
    """Si devuelve None (= ha corrido), el informe tiene que existir."""
    layout = _layout(tmp_path)
    monkeypatch.setattr(cli, "newest_render_stats", lambda dirs: None)
    reason = cli._run_verify_in_pipeline(layout, load_profile())
    assert reason is not None
    assert not (layout.state_dir / "verify.html").exists()
