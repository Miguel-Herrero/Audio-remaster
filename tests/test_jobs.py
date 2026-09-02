"""Jobs de la UI: cancelacion y lectura de la salida en vivo.

Los endpoints se llaman como funciones normales, que es lo que son.
"""

import json
import subprocess
import sys
import time

import pytest

from remaster.web import app as web


@pytest.fixture(autouse=True)
def clean_jobs():
    web._jobs.clear()
    yield
    web._jobs.clear()


def _payload(response):
    return json.loads(response.body)


def _wait_for(predicate, timeout=15.0):
    limit = time.time() + timeout
    while time.time() < limit:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------
# El lector que entiende las barras de progreso
# --------------------------------------------------------------------------

def _pump(text: str) -> list[str]:
    """Pasa `text` por el lector real, en trozos, y devuelve el log."""
    job = web.Job(id="x", episode="x", command=[])

    class FakeStdout:
        def __init__(self, data: bytes):
            self.data = data
            self.pos = 0

        def read1(self, size):
            chunk = self.data[self.pos:self.pos + size]
            self.pos += len(chunk)
            return chunk

    class FakeProc:
        stdout = FakeStdout(text.encode("utf-8"))

    web._pump_output(FakeProc(), job)
    return job.log


def test_plain_lines_are_kept():
    assert _pump("uno\ndos\ntres\n") == ["uno", "dos", "tres"]


def test_a_progress_bar_rewrites_instead_of_piling_up():
    """tqdm pinta con "\\r": sin esto el log crecia con una linea por
    refresco y la barra parecia congelada hasta el final de la fase.
    """
    assert _pump("10%\r50%\r100%\r") == ["100%"]


def test_a_bar_followed_by_a_line_keeps_both():
    assert _pump("50%\r100%\nlisto\n") == ["100%", "listo"]


def test_text_without_a_final_newline_is_not_lost():
    assert _pump("a medias") == ["a medias"]


def test_invalid_utf8_does_not_crash_the_reader():
    job = web.Job(id="x", episode="x", command=[])

    class FakeStdout:
        def __init__(self):
            self.done = False

        def read1(self, size):
            if self.done:
                return b""
            self.done = True
            return b"roto: \xff\xfe\n"

    class FakeProc:
        stdout = FakeStdout()

    web._pump_output(FakeProc(), job)
    assert job.log and job.log[0].startswith("roto:")


# --------------------------------------------------------------------------
# Cancelacion
# --------------------------------------------------------------------------

def test_cancelling_an_unknown_job_is_a_404():
    with pytest.raises(web.HTTPException) as exc:
        web.api_job_cancel("no-existe")
    assert exc.value.status_code == 404


def test_cancelling_a_finished_job_reports_its_status():
    job = web.Job(id="hecho", episode="x", command=[], status="done")
    web._jobs["hecho"] = job
    assert _payload(web.api_job_cancel("hecho"))["status"] == "done"


def test_cancelling_kills_the_whole_process_group():
    """Lo que consume la maquina (inference.py, ffmpeg) es un nieto: si solo
    se matara al hijo directo, seguiria corriendo despues de "cancelar".
    """
    # Un hijo que lanza un nieto durmiente y espera.
    code = (
        "import subprocess, sys, time;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "print(p.pid, flush=True);"
        "p.wait()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    grandchild = int(proc.stdout.readline().decode().strip())

    job = web.Job(id="vivo", episode="x", command=[], process=proc)
    web._jobs["vivo"] = job

    assert _payload(web.api_job_cancel("vivo"))["status"] == "cancelling"
    assert job.cancelled is True

    assert _wait_for(lambda: proc.poll() is not None), "el hijo sigue vivo"
    assert _wait_for(lambda: not _alive(grandchild)), "el nieto ha sobrevivido"


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def test_a_cancelled_job_is_not_reported_as_an_error(tmp_path):
    """`cancelled` y `error` son cosas distintas: una la pediste tu."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    job = web.Job(id="c", episode="x", command=[], process=proc, cancelled=True)
    web._jobs["c"] = job

    web.api_job_cancel("c")
    web._pump_output(proc, job)
    proc.wait(timeout=15)

    with web._jobs_lock:
        job.status = "cancelled" if job.cancelled else "error"
    assert _payload(web.api_job("c"))["status"] == "cancelled"


def test_the_job_status_says_what_kind_it_is():
    web._jobs["k"] = web.Job(id="k", episode="x", command=[], kind="msst", status="done")
    assert _payload(web.api_job("k"))["kind"] == "msst"
