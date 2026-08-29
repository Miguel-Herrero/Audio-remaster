"""UI web local (fase 2). Lista las carpetas de episodio bajo una raiz,
muestra el estado de cada paso leido del manifiesto, y permite lanzar el
pipeline sin salir del navegador.

No duplica logica del pipeline: cada ejecucion lanza el mismo
`python -m remaster.cli run ...` como subproceso y muestra su salida en
vivo — la UI es una capa fina sobre la misma CLI, nunca un segundo
camino de codigo.

Arranque: `remaster ui` (ver remaster/cli.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..cli import STEP_ORDER, run_calibration
from ..layout import EpisodeLayout
from ..manifest import Manifest

app = FastAPI(title="remaster")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


@dataclass
class Job:
    id: str
    episode: str
    args: list[str]
    status: str = "running"  # running | done | error
    log: list[str] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _movies_dir() -> Path:
    return Path(os.environ.get("REMASTER_MOVIES_DIR", str(Path.home() / "Movies"))).expanduser()


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
            "steps_done": sorted(manifest.data.keys()),
            "has_align_png": layout.align_png_path.exists(),
            "has_final_mkv": any(child.glob("*.mkv")) if child.exists() else False,
        })
    return episodes


def _run_job(job: Job) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "remaster.cli", "run", job.episode, *job.args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    job.process = proc
    assert proc.stdout is not None
    for line in proc.stdout:
        with _jobs_lock:
            job.log.append(line.rstrip("\n"))
    proc.wait()
    with _jobs_lock:
        job.status = "done" if proc.returncode == 0 else "error"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _TEMPLATE_PATH.read_text()


@app.get("/api/episodes")
def api_episodes() -> JSONResponse:
    return JSONResponse(_list_episodes())


@app.get("/api/steps")
def api_steps() -> JSONResponse:
    return JSONResponse(STEP_ORDER)


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
    if payload.get("episode_title"):
        args += ["--episode-title", payload["episode_title"]]

    job_id = uuid.uuid4().hex[:12]
    job = Job(id=job_id, episode=episode, args=args)
    with _jobs_lock:
        _jobs[job_id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> JSONResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job no encontrado")
        return JSONResponse({"status": job.status, "log": "\n".join(job.log)})


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


@app.get("/api/episodes/{code}/align.png")
def api_align_png(code: str) -> FileResponse:
    layout = EpisodeLayout(root=_movies_dir() / code)
    if not layout.align_png_path.exists():
        raise HTTPException(404, "sin align.png todavia")
    return FileResponse(layout.align_png_path)
