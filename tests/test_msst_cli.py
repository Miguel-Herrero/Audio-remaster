"""`remaster msst` de punta a punta, con un MSST de mentira.

Se sustituye `inference.py` por un script que escribe lo que escribiria el
de verdad. Asi se puede comprobar lo que rodea a la inferencia — el
directorio temporal, el aplanado de nombres, la limpieza — sin cargar un
modelo de 142 MB en cada test.
"""

import glob
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


def _temporales() -> list[str]:
    return glob.glob(str(Path(tempfile.gettempdir()) / "remaster-msst-*"))


@pytest.fixture(autouse=True)
def sin_temporales_previos():
    """Los temporales son la mitad de lo que se comprueba aqui."""
    for old in _temporales():
        subprocess.run(["rm", "-rf", old], check=False)
    yield


@pytest.fixture
def fake_msst(tmp_path):
    """Un clon de MSST cuyo `inference.py` escribe pistas de mentira."""
    root = tmp_path / "msst"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").symlink_to(sys.executable)
    (root / "configs").mkdir()
    (root / "configs" / "demo.yaml").write_text("training: {}\n")

    (root / "inference.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
        "out = Path(args['--store_dir'])\n"
        "nombre = next(p.stem for p in Path(args['--input_folder']).iterdir())\n"
        "for instr in ('speech', 'music'):\n"
        "    (out / f'{nombre}__{instr}.flac').write_bytes(b'x' * 2048)\n"
        "print('Elapsed time: 0.1 seconds.')\n"
    )
    return root


@pytest.fixture
def catalogo(tmp_path, fake_msst):
    path = tmp_path / "msst_models.local.toml"
    path.write_text(
        '[[model]]\n'
        'id = "demo"\n'
        'name = "Demo"\n'
        'model_type = "bs_roformer"\n'
        'config = "repo:configs/demo.yaml"\n'
        'checkpoint = "data:pesos.ckpt"\n'
        'stems = ["speech", "music"]\n',
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pesos.ckpt").write_bytes(b"pesos")
    return path


def _run(tmp_path, catalogo, fake_msst, audio, extra=(), timeout=60):
    env = {
        **os.environ,
        "MSST_REPO_DIR": str(fake_msst),
        "MSST_DATA_DIR": str(tmp_path / "data"),
        "MSST_CATALOG": str(catalogo),
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "remaster.cli", "msst", str(audio),
         "--model", "demo", "--out", str(tmp_path / "salida"), *extra],
        capture_output=True, text=True, env=env, timeout=timeout,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "una escena.flac"
    path.write_bytes(b"audio")
    return path


def test_the_output_names_are_flat(tmp_path, catalogo, fake_msst, audio):
    """MSST mete cada pista en su subcarpeta; aqui salen planas y con el
    modelo en el nombre, para poder comparar dos modelos en la misma carpeta.
    """
    res = _run(tmp_path, catalogo, fake_msst, audio)
    assert res.returncode == 0, res.stdout + res.stderr

    producidos = sorted(p.name for p in (tmp_path / "salida").iterdir())
    assert producidos == ["una escena_demo_music.flac", "una escena_demo_speech.flac"]


def test_the_input_is_isolated_in_a_temp_folder(tmp_path, catalogo, fake_msst, audio):
    """`inference.py` hace un glob de la carpeta entera: si se le pasara la
    del audio, se llevaria por delante todo lo que hubiera al lado.
    """
    (audio.parent / "otra cosa.txt").write_text("no me toques")
    res = _run(tmp_path, catalogo, fake_msst, audio)
    assert res.returncode == 0
    assert (audio.parent / "otra cosa.txt").exists()
    assert len(list((tmp_path / "salida").iterdir())) == 2


def test_the_temp_folder_is_removed(tmp_path, catalogo, fake_msst, audio):
    _run(tmp_path, catalogo, fake_msst, audio)
    assert _temporales() == []


def test_the_temp_folder_is_removed_even_when_inference_fails(
    tmp_path, catalogo, fake_msst, audio
):
    (fake_msst / "inference.py").write_text("import sys; sys.exit(3)")
    res = _run(tmp_path, catalogo, fake_msst, audio)
    assert res.returncode != 0
    assert _temporales() == []


def test_the_temp_folder_is_removed_when_cancelled(tmp_path, catalogo, fake_msst, audio):
    """Cancelar manda un SIGTERM, que por defecto mata el proceso sin pasar
    por el `finally`: sin el manejador, el temporal se quedaba para siempre.
    """
    (fake_msst / "inference.py").write_text("import time; time.sleep(60)")
    proc = subprocess.Popen(
        [sys.executable, "-m", "remaster.cli", "msst", str(audio),
         "--model", "demo", "--out", str(tmp_path / "salida")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, "MSST_REPO_DIR": str(fake_msst),
             "MSST_DATA_DIR": str(tmp_path / "data"),
             "MSST_CATALOG": str(catalogo), "NO_COLOR": "1"},
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    limite = time.time() + 20
    while not _temporales() and time.time() < limite:
        time.sleep(0.1)
    assert _temporales(), "el temporal no llego a crearse"

    proc.terminate()
    proc.wait(timeout=20)
    assert _temporales() == []


def test_a_missing_file_is_rejected_before_launching_anything(
    tmp_path, catalogo, fake_msst
):
    res = _run(tmp_path, catalogo, fake_msst, tmp_path / "no-existe.flac")
    assert res.returncode != 0
    assert "No existe" in res.stdout
    assert _temporales() == []


def test_a_missing_checkpoint_points_at_the_fetch_command(
    tmp_path, catalogo, fake_msst, audio
):
    (tmp_path / "data" / "pesos.ckpt").unlink()
    res = _run(tmp_path, catalogo, fake_msst, audio)
    assert res.returncode != 0
    assert "msst-fetch demo" in res.stdout


def test_an_unknown_format_is_rejected(tmp_path, catalogo, fake_msst, audio):
    res = _run(tmp_path, catalogo, fake_msst, audio, extra=["--format", "mp3"])
    assert res.returncode != 0
    assert "Formato desconocido" in res.stdout
