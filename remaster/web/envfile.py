"""Leer y escribir un `.env` de `CLAVE=valor`, sin dependencias.

Solo para que el token de MVSEP se pueda meter una vez desde la UI y
quedarse puesto, en vez de tener que exportar `MVSEP_API_TOKEN` en cada
shell. `python-dotenv` haria esto y mas, pero traerlo por veinte lineas
no compensa — y la parte que de verdad importa (escribir preservando el
resto del fichero) tampoco la regala.

Al escribir se conserva el fichero tal cual salvo la linea de la clave
tocada: comentarios, orden y lineas en blanco siguen donde estaban. Un
`.env` suele tener notas al lado de cada valor y reescribirlo entero
desde un dict las borraria.
"""

from __future__ import annotations

import os
from pathlib import Path

# Un `.env` es un fichero de secretos: se crea sin permisos para el grupo
# ni para otros usuarios.
_SECRET_MODE = 0o600


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _split_line(line: str) -> tuple[str, str] | None:
    """`("CLAVE", "valor")`, o None si la linea no asigna nada."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    # `export CLAVE=valor` es valido en los .env que uno acaba copiando de
    # un script de shell.
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    key, _, value = stripped.partition("=")
    key = key.strip()
    if not key:
        return None
    return key, _strip_quotes(value.strip())


def read_env(path: Path) -> dict[str, str]:
    """Las claves del fichero. Si no existe o no se puede leer, `{}`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        pair = _split_line(line)
        if pair is not None:
            values[pair[0]] = pair[1]
    return values


def _needs_quotes(value: str) -> bool:
    return value != value.strip() or any(c in value for c in " \t#\"'")


def _format_line(key: str, value: str) -> str:
    if _needs_quotes(value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    return f"{key}={value}"


def set_env_value(path: Path, key: str, value: str) -> None:
    """Deja `key=value` en el fichero, creandolo si hace falta.

    Si la clave ya estaba, se sustituye esa linea en su sitio (asi el
    comentario que la acompaña sigue teniendo sentido). Si no estaba, se
    añade al final.
    """
    path = Path(path)
    try:
        original = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        original = ""

    new_line = _format_line(key, value)
    lines = original.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        pair = _split_line(line)
        if pair is not None and pair[0] == key:
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Solo al crearlo: si el fichero ya existia, respetamos los permisos
    # que el usuario le haya puesto.
    if not original:
        os.chmod(path, _SECRET_MODE)
