"""Traza de pasos en NDJSON, para que la UI pueda dibujar el progreso.

La UI necesita saber en que paso va y como acabo cada uno. Sacar eso
parseando la salida de `rich` seria atarla al formato de unos
`console.print` pensados para leerse, no para maquinas: cambiar una
palabra romperia el timeline. Asi que `remaster run --events <fichero>`
escribe una linea JSON por transicion, en paralelo a la salida humana,
que no cambia.

    {"step":"separate","state":"running","t":1756...}
    {"step":"separate","state":"done","outcome":"ran","seconds":412.7}
    {"step":"verify","state":"done","outcome":"skipped","reason":"..."}

Sin `--events` todo esto es un no-op: `EventLog(None)` no abre nada ni
escribe nada, y la CLI se comporta exactamente como antes.

Cada linea se escribe y se cierra en el acto (append + flush) porque
quien lee es otro proceso, releyendo el fichero mientras este corre.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Como acabo un paso:
#   ran     — se ejecuto y produjo salida nueva
#   noop    — estaba al dia; el manifiesto lo dio por hecho
#   skipped — no se hizo, y hay un motivo que enseñar
#   error   — reventó
OUTCOMES = ("ran", "noop", "skipped", "error")


class EventLog:
    """Traza de un `remaster run`. Un paso abierto como mucho a la vez.

    Cerrar el paso anterior al abrir el siguiente (en vez de exigir un
    `end()` explicito en cada bloque) evita sembrar la CLI de llamadas
    simetricas faciles de olvidar en un `return` temprano. Los pasos son
    sentencias consecutivas, asi que lo que se pierde de duracion es el
    tiempo entre un bloque y el siguiente: nada.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path else None
        self._current: str | None = None
        self._started_at: float = 0.0
        # Un paso puede reportar varios resultados (extract lo hace con
        # es/en/ja). El del paso es el mas "fuerte" de ellos.
        self._outcomes: list[str] = []

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def _write(self, payload: dict) -> None:
        if self.path is None:
            return
        payload["t"] = round(time.time(), 3)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            # La traza es para mirar, no para el resultado. Si el fichero
            # se va (temporal borrado, disco lleno), el render sigue.
            pass

    def start(self, step: str) -> None:
        self.close()
        self._current = step
        self._started_at = time.time()
        self._outcomes = []
        self._write({"step": step, "state": "running"})

    def note(self, outcome: str) -> None:
        """Resultado de una parte del paso en curso (ver `_outcomes`)."""
        if self._current is not None and outcome in OUTCOMES:
            self._outcomes.append(outcome)

    def _resolve(self) -> str:
        for outcome in ("error", "ran", "noop"):
            if outcome in self._outcomes:
                return outcome
        return "skipped" if self._outcomes else "ran"

    def close(self, outcome: str | None = None, reason: str | None = None) -> None:
        if self._current is None:
            return
        payload = {
            "step": self._current,
            "state": "done",
            "outcome": outcome or self._resolve(),
            "seconds": round(time.time() - self._started_at, 2),
        }
        if reason:
            payload["reason"] = reason
        self._current = None
        self._outcomes = []
        self._write(payload)

    def skip(self, step: str, reason: str) -> None:
        """Un paso que ni se intenta, con el motivo a la vista."""
        self.close()
        self._write({"step": step, "state": "done", "outcome": "skipped", "reason": reason, "seconds": 0})

    def fail(self, reason: str) -> None:
        self.close(outcome="error", reason=reason)


def read_events(path: Path) -> list[dict]:
    """Las lineas validas del fichero. Tolera la ultima a medio escribir:
    quien lee lo hace mientras el otro proceso escribe.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, UnicodeDecodeError):
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
