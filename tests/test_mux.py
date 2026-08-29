"""Tests del constructor de comandos mkvmerge (remaster/steps/mux.py).

El bug del script viejo: `--track-order` se construia incrementando un
indice compartido entre EN y ES, y se descuadraba cuando faltaba la
pista japonesa. Aqui el orden de ficheros es fijo por diseño (0=Blu-ray,
1=ES, 2=EN, 3=JA si existe), asi que --track-order no depende de ninguna
aritmetica condicional.
"""

from __future__ import annotations

from pathlib import Path

from remaster.profile import load_profile
from remaster.steps.mux import build_mux_command


def _paths():
    return (
        Path("/ep/bluray.mkv"), Path("/ep/en_source.flac"),
        Path("/ep/es_reconstructed.flac"), Path("/ep/ja_source.flac"),
    )


def test_track_order_with_japanese():
    bluray, en, es, ja = _paths()
    profile = load_profile()
    cmd = build_mux_command("mkvmerge", bluray, en, es, ja, [3, 5], Path("/out.mkv"), "Titulo", profile)

    order_idx = cmd.index("--track-order")
    track_order = cmd[order_idx + 1]
    assert track_order == "0:0,1:0,2:0,3:0,0:3,0:5"


def test_track_order_without_japanese():
    bluray, en, es, _ja = _paths()
    profile = load_profile()
    cmd = build_mux_command("mkvmerge", bluray, en, es, None, [3, 5], Path("/out.mkv"), "Titulo", profile)

    order_idx = cmd.index("--track-order")
    track_order = cmd[order_idx + 1]
    # Sin JA, el orden no se descuadra: simplemente no aparece el file 3.
    assert track_order == "0:0,1:0,2:0,0:3,0:5"


def test_track_order_without_subtitles():
    bluray, en, es, ja = _paths()
    profile = load_profile()
    cmd = build_mux_command("mkvmerge", bluray, en, es, ja, [], Path("/out.mkv"), None, profile)

    order_idx = cmd.index("--track-order")
    assert cmd[order_idx + 1] == "0:0,1:0,2:0,3:0"


def test_file_positions_are_fixed_regardless_of_japanese():
    """El fichero Blu-ray siempre es el primero, ES siempre el tercero
    (file 2) — no depende de si hay JA o no, a diferencia del script viejo.
    """
    bluray, en, es, ja = _paths()
    profile = load_profile()

    cmd_with_ja = build_mux_command("mkvmerge", bluray, en, es, ja, [], Path("/out.mkv"), None, profile)
    cmd_without_ja = build_mux_command("mkvmerge", bluray, en, es, None, [], Path("/out.mkv"), None, profile)

    assert cmd_with_ja[cmd_with_ja.index(str(bluray)) - 1] != "--language"  # bluray es file 0, sin --language
    assert str(es) in cmd_with_ja and str(es) in cmd_without_ja
    # La posicion relativa de ES en la lista de argumentos no cambia.
    assert cmd_with_ja.index(str(es)) == cmd_without_ja.index(str(es))


def test_es_is_file_1_and_en_is_file_2():
    """Orden Video -> ES -> EN -> JA, igual que en SH1984/v1.0.1.md (no
    Video -> EN -> ES): ES debe aparecer en el comando antes que EN.
    """
    bluray, en, es, ja = _paths()
    profile = load_profile()
    cmd = build_mux_command("mkvmerge", bluray, en, es, ja, [], Path("/out.mkv"), None, profile)
    assert cmd.index(str(es)) < cmd.index(str(en))


def test_title_omitted_when_none():
    bluray, en, es, ja = _paths()
    profile = load_profile()
    cmd = build_mux_command("mkvmerge", bluray, en, es, ja, [], Path("/out.mkv"), None, profile)
    assert "--title" not in cmd
