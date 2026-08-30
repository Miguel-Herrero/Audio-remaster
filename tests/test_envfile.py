"""Tests de remaster/web/envfile.py.

Lo que de verdad importa aqui es que escribir una clave NO se lleve por
delante el resto del fichero: un `.env` suele llevar comentarios al lado
de cada valor, y reescribirlo desde un dict los borraria.
"""

from __future__ import annotations

import stat

from remaster.web.envfile import read_env, set_env_value


def test_reads_plain_pairs(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nB=dos\n")
    assert read_env(p) == {"A": "1", "B": "dos"}


def test_ignores_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# un comentario\n\nA=1\n   \n# otro\nB=2\n")
    assert read_env(p) == {"A": "1", "B": "2"}


def test_strips_quotes_and_export(tmp_path):
    p = tmp_path / ".env"
    p.write_text('A="con espacios"\nB=\'simples\'\nexport C=tres\n')
    assert read_env(p) == {"A": "con espacios", "B": "simples", "C": "tres"}


def test_missing_file_is_empty(tmp_path):
    assert read_env(tmp_path / "no-existe") == {}


def test_writes_a_new_file(tmp_path):
    p = tmp_path / ".env"
    set_env_value(p, "MVSEP_API_TOKEN", "abc123")
    assert read_env(p) == {"MVSEP_API_TOKEN": "abc123"}


def test_new_file_is_not_world_readable(tmp_path):
    """Es un fichero de secretos."""
    p = tmp_path / ".env"
    set_env_value(p, "TOKEN", "s3cr3t")
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_replacing_a_key_preserves_everything_else(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# cabecera\nA=1\n\n# el token de mvsep\nTOKEN=viejo\nZ=9\n")
    set_env_value(p, "TOKEN", "nuevo")
    assert p.read_text() == "# cabecera\nA=1\n\n# el token de mvsep\nTOKEN=nuevo\nZ=9\n"


def test_new_key_is_appended(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    set_env_value(p, "B", "2")
    assert p.read_text() == "A=1\nB=2\n"
    assert read_env(p) == {"A": "1", "B": "2"}


def test_value_with_spaces_round_trips(tmp_path):
    p = tmp_path / ".env"
    set_env_value(p, "K", "dos palabras")
    assert '"' in p.read_text()
    assert read_env(p)["K"] == "dos palabras"


def test_existing_file_permissions_are_left_alone(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    p.chmod(0o644)
    set_env_value(p, "A", "2")
    assert stat.S_IMODE(p.stat().st_mode) == 0o644
