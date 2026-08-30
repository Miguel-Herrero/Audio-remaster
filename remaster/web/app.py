"""UI web local. Lista las carpetas de episodio bajo una raiz, muestra el
estado de cada paso leido del manifiesto, y permite lanzar el pipeline sin
salir del navegador.

No duplica logica del pipeline: cada ejecucion lanza el mismo
`python -m remaster.cli run ...` como subproceso y muestra su salida en
vivo — la UI es una capa fina sobre la misma CLI, nunca un segundo camino
de codigo. Lo mismo con la prosa: que hace cada paso y que salvaguarda
tiene sale de `remaster/steps_meta.py`, no del HTML.

La carpeta base ya no es solo `REMASTER_MOVIES_DIR`: se puede cambiar
desde el navegador (`POST /api/root`) y se recuerda entre arranques, para
que abrir la UI no exija saberse una variable de entorno. Lo mismo con el
token de MVSEP, que se lee y se guarda en un `.env`.

Arranque: `remaster ui` (ver remaster/cli.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..cli import STEP_ORDER, stats_search_dirs, run_calibration
from ..events import read_events
from ..layout import EpisodeLayout
from ..manifest import Manifest
from ..reaper.render_stats import newest_render_stats
from ..steps.project import project_hand_edited
from ..steps_meta import STEP_INFO
from .envfile import read_env, set_env_value

app = FastAPI(title="remaster")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"

# Preferencias de la UI (carpeta base, recientes, separador). Fuera del
# repo a proposito: son de esta maquina, no del proyecto.
_PREFS_PATH = Path.home() / ".config" / "remaster" / "ui.json"

# El `.env` del que se lee (y en el que se guarda) el token de MVSEP: la
# raiz del repo, que es donde uno lo espera.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

_MAX_RECENT_ROOTS = 8


@dataclass
class Job:
    id: str
    episode: str
    command: list[str]  # argv completo tras `-m remaster.cli`, p.ej. ["run", "<ep>", "--from", ...]
    status: str = "running"  # running | done | error
    log: list[str] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    events_path: Optional[Path] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()

# Token en memoria del proceso. Se pasa al subproceso por entorno y nunca
# por argv, que cualquiera puede leer con `ps`.
_mvsep_token: Optional[str] = None


# --------------------------------------------------------------------------
# Preferencias y carpeta base
# --------------------------------------------------------------------------

def _load_prefs() -> dict:
    try:
        return json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, PermissionError, json.JSONDecodeError):
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        # Recordar la carpeta es una comodidad; si el disco dice que no,
        # la UI sigue funcionando con lo que haya en memoria.
        pass


def _default_root() -> Path:
    """Primero lo que dijo `remaster ui --movies-dir`/el entorno, luego lo
    ultimo que se uso en el navegador, y si no ~/Movies.
    """
    from_env = os.environ.get("REMASTER_MOVIES_DIR")
    if from_env:
        return Path(from_env).expanduser()
    recent = _load_prefs().get("recent_roots") or []
    if recent:
        return Path(recent[0]).expanduser()
    return Path.home() / "Movies"


_root: Path = _default_root()


def _movies_dir() -> Path:
    return _root


def _count_episodes(root: Path) -> int:
    if not root.is_dir():
        return 0
    try:
        return sum(1 for c in root.iterdir() if c.is_dir() and (c / "00_sources").is_dir())
    except PermissionError:
        return 0


def _root_payload() -> dict:
    root = _movies_dir()
    return {
        "path": str(root),
        "exists": root.is_dir(),
        "episode_count": _count_episodes(root),
        "recent": _load_prefs().get("recent_roots") or [],
        "home": str(Path.home()),
    }


@app.get("/api/root")
def api_root_get() -> JSONResponse:
    return JSONResponse(_root_payload())


@app.post("/api/root")
def api_root_set(payload: dict) -> JSONResponse:
    """Cambia la carpeta base. Valida antes de aceptar: decir 'ninguna
    carpeta' con un mensaje util es mejor que una lista vacia sin motivo.
    """
    global _root
    raw = (payload.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "falta 'path'")
    # Arrastrar del Finder deja un file:// URL; el navegador ya lo
    # decodifica, pero el esquema hay que quitarlo aqui igualmente por si
    # llega crudo desde otro cliente.
    if raw.startswith("file://"):
        from urllib.parse import unquote, urlparse
        raw = unquote(urlparse(raw).path)
    path = Path(raw).expanduser()
    if not path.exists():
        raise HTTPException(400, f"no existe: {path}")
    if not path.is_dir():
        raise HTTPException(400, f"no es una carpeta: {path}")

    _root = path.resolve()
    prefs = _load_prefs()
    recent = [str(_root)] + [r for r in (prefs.get("recent_roots") or []) if r != str(_root)]
    prefs["recent_roots"] = recent[:_MAX_RECENT_ROOTS]
    _save_prefs(prefs)
    return JSONResponse(_root_payload())


# --------------------------------------------------------------------------
# Token de MVSEP
# --------------------------------------------------------------------------

def _token_source() -> tuple[Optional[str], str]:
    """(token, de donde salio). Orden: lo que se metio en esta sesion,
    luego el `.env`, luego el entorno del proceso.
    """
    if _mvsep_token:
        return _mvsep_token, "sesion"
    from_file = read_env(_ENV_PATH).get("MVSEP_API_TOKEN")
    if from_file:
        return from_file, ".env"
    from_env = os.environ.get("MVSEP_API_TOKEN")
    if from_env:
        return from_env, "entorno"
    return None, ""


@app.get("/api/secrets")
def api_secrets_get() -> JSONResponse:
    """Si hay token y de donde sale. Nunca devuelve el token: la UI solo
    necesita saber que esta puesto para no volver a pedirlo.
    """
    token, source = _token_source()
    return JSONResponse({
        "mvsep_token_set": bool(token),
        "source": source,
        "env_path": str(_ENV_PATH),
    })


@app.post("/api/secrets")
def api_secrets_set(payload: dict) -> JSONResponse:
    global _mvsep_token
    token = (payload.get("mvsep_token") or "").strip()
    if not token:
        raise HTTPException(400, "falta 'mvsep_token'")
    _mvsep_token = token
    if payload.get("remember"):
        try:
            set_env_value(_ENV_PATH, "MVSEP_API_TOKEN", token)
        except OSError as exc:
            raise HTTPException(500, f"no he podido escribir {_ENV_PATH}: {exc}") from exc
    return api_secrets_get()


# --------------------------------------------------------------------------
# Episodios
# --------------------------------------------------------------------------

def _render_stats_info(layout: EpisodeLayout) -> dict:
    """Que sabemos del render_stats.html que REAPER deja en el temporal.

    La UI lo enseña porque el dry run es manual: sin el, `verify` se omite,
    y conviene ver de cuando es la medida antes de fiarse.
    """
    path = newest_render_stats(stats_search_dirs(layout))
    if path is None:
        return {"found": False}
    mtime = path.stat().st_mtime
    return {
        "found": True,
        "path": str(path),
        "age_minutes": round((time.time() - mtime) / 60),
        "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
        # El proyecto guardado despues de medir invalida la medida: es el
        # error que mas veces lleva a mirar un informe caducado.
        "stale_vs_project": _project_saved_after(layout, mtime),
    }


def _project_saved_after(layout: EpisodeLayout, mtime: float) -> bool:
    try:
        return layout.rpp_edit.stat().st_mtime > mtime
    except OSError:
        return False


def _episode_steps(layout: EpisodeLayout, manifest: Manifest) -> dict[str, dict]:
    """Lo que ya esta hecho de cada paso, para poblar el timeline al abrir
    el episodio en vez de esperar a una ejecucion.

    Un paso puede tener varias entradas en el manifiesto, una por idioma
    (`extract:es`, `extract:en`, `separate:en`…): cuentan como el mismo
    paso, y la duracion del paso es la suma. Buscar solo la clave exacta
    daba por no hecho medio pipeline.

    Ni `ingest` ni `verify` escriben en el manifiesto — el primero solo
    clasifica y el segundo solo lee —, asi que su estado se deduce de lo
    que dejan a la vista.
    """
    out: dict[str, dict] = {}
    for key in STEP_ORDER:
        entries = [
            entry for name, entry in manifest.data.items()
            if name == key or name.startswith(f"{key}:")
        ]
        if not entries:
            continue
        seconds = sum(e.get("duration_seconds") or 0.0 for e in entries)
        out[key] = {
            "done": True,
            "seconds": round(seconds, 2),
            "recorded_at": max((e.get("recorded_at") or "") for e in entries) or None,
            "parts": len(entries),
        }

    if layout.sources.is_dir() and any(layout.sources.iterdir()):
        out.setdefault("ingest", {"done": True, "seconds": None, "recorded_at": None, "parts": 1})
    if (layout.state_dir / "verify.html").exists():
        out.setdefault("verify", {"done": True, "seconds": None, "recorded_at": None, "parts": 1})
    return out


def _list_episodes() -> list[dict]:
    root = _movies_dir()
    if not root.is_dir():
        return []
    episodes = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "00_sources").is_dir():
            continue
        layout = EpisodeLayout(root=child)
        manifest = Manifest(layout.manifest_path)
        episodes.append({
            "code": layout.code,
            "path": str(child),
            "steps": _episode_steps(layout, manifest),
            "steps_total": len(STEP_ORDER),
            "has_align_png": layout.align_png_path.exists(),
            "has_final_mkv": any(child.glob("*.mkv")),
            "has_rpp": layout.rpp_edit.exists(),
            "has_verify": (layout.state_dir / "verify.html").exists(),
            "rpp_hand_edited": project_hand_edited(layout, manifest),
        })
    return episodes


@app.get("/api/episodes")
def api_episodes() -> JSONResponse:
    return JSONResponse(_list_episodes())


@app.get("/api/steps")
def api_steps() -> JSONResponse:
    """Los pasos en orden, cada uno con que hace y que protege."""
    return JSONResponse([info.as_dict() for info in STEP_INFO])


@app.get("/api/episodes/{code}/render-stats")
def api_render_stats(code: str) -> JSONResponse:
    return JSONResponse(_render_stats_info(EpisodeLayout(root=_movies_dir() / code)))


@app.get("/api/episodes/{code}/verify.html")
def api_verify_report(code: str) -> FileResponse:
    path = EpisodeLayout(root=_movies_dir() / code).state_dir / "verify.html"
    if not path.exists():
        raise HTTPException(404, "todavia no hay informe: ejecuta el paso «verify»")
    return FileResponse(path, media_type="text/html")


@app.get("/api/episodes/{code}/align.png")
def api_align_png(code: str) -> FileResponse:
    layout = EpisodeLayout(root=_movies_dir() / code)
    if not layout.align_png_path.exists():
        raise HTTPException(404, "sin align.png todavia")
    return FileResponse(layout.align_png_path)


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------

def _run_job(job: Job) -> None:
    env = dict(os.environ)
    token, _ = _token_source()
    if token:
        # Por entorno, no por argv: `ps aux` enseña los argumentos de
        # cualquier proceso a cualquier usuario de la maquina.
        env["MVSEP_API_TOKEN"] = token
    # rich colorea con secuencias ANSI que en un <pre> del navegador se
    # ven como basura ("[1mverify[0m"). Se le pide que no coloree, y
    # se le da mas ancho que los 80 que asume al no hablar con un
    # terminal, que parten las tablas por la mitad.
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["COLUMNS"] = "110"

    proc = subprocess.Popen(
        [sys.executable, "-m", "remaster.cli", *job.command],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
    )
    job.process = proc
    assert proc.stdout is not None
    for line in proc.stdout:
        with _jobs_lock:
            job.log.append(line.rstrip("\n"))
    proc.wait()
    with _jobs_lock:
        job.status = "done" if proc.returncode == 0 else "error"
        job.finished_at = time.time()


def _start_job(episode: str, command: list[str], with_events: bool = False) -> JSONResponse:
    job_id = uuid.uuid4().hex[:12]
    events_path = None
    if with_events:
        fd, name = tempfile.mkstemp(prefix=f"remaster-{job_id}-", suffix=".ndjson")
        os.close(fd)
        events_path = Path(name)
        command = [*command, "--events", str(events_path)]
    job = Job(id=job_id, episode=episode, command=command, events_path=events_path)
    with _jobs_lock:
        _jobs[job_id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.post("/api/run")
def api_run(payload: dict) -> JSONResponse:
    episode = payload.get("episode")
    if not episode:
        raise HTTPException(400, "falta 'episode'")

    args = [
        "--from", payload.get("from_step", "ingest"),
        "--to", payload.get("to_step", "mux"),
        "--separator", payload.get("separator", "local"),
    ]
    if payload.get("force"):
        args.append("--force")
    if payload.get("final_name"):
        args += ["--name", payload["final_name"]]

    return _start_job(episode, ["run", episode, *args], with_events=True)


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> JSONResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job no encontrado")
        payload = {
            "status": job.status,
            "log": "\n".join(job.log),
            "events": read_events(job.events_path) if job.events_path else [],
            "seconds": round((job.finished_at or time.time()) - job.started_at, 1),
        }
    return JSONResponse(payload)


@app.post("/api/calibrate")
def api_calibrate(payload: dict) -> JSONResponse:
    """Misma logica que `remaster calibrate` (remaster/cli.py:run_calibration)
    — la UI no reimplementa el ajuste, solo lo envuelve en HTTP.
    """
    episode = payload.get("episode")
    points = payload.get("points") or []
    if not episode:
        raise HTTPException(400, "falta 'episode'")
    if len(points) < 2:
        raise HTTPException(400, "hacen falta al menos 2 puntos")

    try:
        es_times = [float(p["es"]) for p in points]
        en_times = [float(p["en"]) for p in points]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"puntos invalidos: {exc}") from exc

    try:
        result, step_ran = run_calibration(
            Path(episode), es_times, en_times,
            against_offset=float(payload.get("against_offset", 0.0)),
            apply=bool(payload.get("apply", False)),
            force=bool(payload.get("force", False)),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return JSONResponse({
        "offset_seconds": result.offset_seconds,
        "drift_slope": result.drift_slope,
        "residuals": list(result.residuals),
        "applied": bool(payload.get("apply", False)),
        "step_ran": step_ran,
    })


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")
