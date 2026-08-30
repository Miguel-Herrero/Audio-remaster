"""Tests de la API de la UI (remaster/web/app.py).

Los endpoints se llaman como funciones normales, que es lo que son: no
hace falta `fastapi.testclient` (que arrastraria httpx) para comprobar la
logica, y lo que se prueba aqui es logica, no HTTP.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from remaster.web import app as web


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Sin esto, los tests escribirian el .env y las preferencias de
    verdad del usuario que los ejecute.
    """
    monkeypatch.setattr(web, "_PREFS_PATH", tmp_path / "prefs.json")
    monkeypatch.setattr(web, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(web, "_root", tmp_path / "movies")
    monkeypatch.setattr(web, "_mvsep_token", None)
    monkeypatch.delenv("MVSEP_API_TOKEN", raising=False)


def _payload(response):
    return json.loads(response.body)


def _episode(root, code):
    ep = root / code
    (ep / "00_sources").mkdir(parents=True)
    return ep


# --------------------------------------------------------------- carpeta base

def test_root_reports_a_missing_folder(tmp_path):
    data = _payload(web.api_root_get())
    assert data["exists"] is False
    assert data["episode_count"] == 0


def test_setting_the_root_counts_episodes(tmp_path):
    root = tmp_path / "movies"
    _episode(root, "s01e01")
    _episode(root, "s01e02")
    (root / "no-es-episodio").mkdir()

    data = _payload(web.api_root_set({"path": str(root)}))
    assert data["exists"] is True
    assert data["episode_count"] == 2


def test_setting_a_missing_root_is_rejected(tmp_path):
    with pytest.raises(HTTPException) as exc:
        web.api_root_set({"path": str(tmp_path / "no-existe")})
    assert exc.value.status_code == 400
    assert "no existe" in exc.value.detail


def test_setting_a_file_as_root_is_rejected(tmp_path):
    f = tmp_path / "algo.txt"
    f.write_text("x")
    with pytest.raises(HTTPException) as exc:
        web.api_root_set({"path": str(f)})
    assert "no es una carpeta" in exc.value.detail


def test_empty_root_is_rejected():
    with pytest.raises(HTTPException):
        web.api_root_set({"path": "  "})


def test_file_url_from_a_finder_drop_is_accepted(tmp_path):
    """Arrastrar del Finder deja un file:// con la ruta escapada."""
    root = tmp_path / "mis videos"
    _episode(root, "s01e01")
    data = _payload(web.api_root_set({"path": f"file://{root}".replace(" ", "%20")}))
    assert data["path"] == str(root.resolve())


def test_root_is_remembered_as_recent(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    web.api_root_set({"path": str(first)})
    data = _payload(web.api_root_set({"path": str(second)}))
    assert data["recent"][:2] == [str(second.resolve()), str(first.resolve())]


def test_recent_has_no_duplicates(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    web.api_root_set({"path": str(d)})
    data = _payload(web.api_root_set({"path": str(d)}))
    assert data["recent"].count(str(d.resolve())) == 1


# --------------------------------------------------------------- secretos

def test_secrets_report_no_token_by_default():
    data = _payload(web.api_secrets_get())
    assert data["mvsep_token_set"] is False
    assert data["source"] == ""


def test_secrets_never_return_the_token():
    web.api_secrets_set({"mvsep_token": "s3cr3t-abc", "remember": False})
    body = web.api_secrets_get().body.decode()
    assert "s3cr3t-abc" not in body
    assert json.loads(body)["mvsep_token_set"] is True


def test_remember_writes_the_env_file(tmp_path):
    web.api_secrets_set({"mvsep_token": "tok-123", "remember": True})
    assert "tok-123" in (tmp_path / ".env").read_text()


def test_without_remember_nothing_is_written(tmp_path):
    web.api_secrets_set({"mvsep_token": "tok-123", "remember": False})
    assert not (tmp_path / ".env").exists()


def test_token_is_read_back_from_the_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MVSEP_API_TOKEN=desde-fichero\n")
    monkeypatch.setattr(web, "_mvsep_token", None)
    token, source = web._token_source()
    assert (token, source) == ("desde-fichero", ".env")


def test_empty_token_is_rejected():
    with pytest.raises(HTTPException):
        web.api_secrets_set({"mvsep_token": "   "})


# --------------------------------------------------------------- pasos y episodios

def test_steps_carry_their_description():
    steps = _payload(web.api_steps())
    keys = [s["key"] for s in steps]
    assert keys == web.STEP_ORDER
    assert all(s["summary"] and s["safeguard"] for s in steps)
    assert next(s for s in steps if s["key"] == "verify")["manual"] is True


def test_episodes_list_only_folders_with_sources(tmp_path):
    root = tmp_path / "movies"
    _episode(root, "s01e01")
    (root / "suelto").mkdir()
    web.api_root_set({"path": str(root)})

    episodes = _payload(web.api_episodes())
    assert [e["code"] for e in episodes] == ["s01e01"]
    assert episodes[0]["steps"] == {}
    assert episodes[0]["rpp_hand_edited"] is False
    assert episodes[0]["steps_total"] == len(web.STEP_ORDER)


def test_episodes_report_manifest_progress(tmp_path):
    root = tmp_path / "movies"
    ep = _episode(root, "s01e01")
    state = ep / ".remaster"
    state.mkdir()
    (state / "manifest.json").write_text(json.dumps({
        "extract": {"outputs": {}, "duration_seconds": 41.2, "recorded_at": "2026-08-30T10:00:00+0200"},
    }))
    web.api_root_set({"path": str(root)})

    steps = _payload(web.api_episodes())[0]["steps"]
    assert steps["extract"]["done"] is True
    assert steps["extract"]["seconds"] == 41.2
