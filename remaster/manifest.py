"""Estado en disco (.remaster/manifest.json) que da determinismo y
reanudacion: por cada paso se registra que entradas (hash), que parametros
y que version de herramientas lo produjeron. Re-ejecutar un paso cuyas
entradas/parametros no cambiaron y cuyas salidas siguen intactas es un
no-op. `force=True` lo invalida.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class StepResult:
    ran: bool  # False si se salto por ser no-op
    outputs: dict[Path, str] = field(default_factory=dict)


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True, ensure_ascii=False))

    def get(self, step_name: str) -> dict | None:
        return self.data.get(step_name)

    def _is_up_to_date(
        self,
        step_name: str,
        input_hashes: dict[str, str],
        params: dict,
        output_paths: list[Path],
    ) -> bool:
        prev = self.data.get(step_name)
        if not prev:
            return False
        if prev.get("params") != params:
            return False
        if prev.get("inputs") != input_hashes:
            return False
        prev_outputs = prev.get("outputs", {})
        for o in output_paths:
            if not o.exists():
                return False
            recorded = prev_outputs.get(str(o))
            if recorded is None or sha256_file(o) != recorded:
                return False
        return True

    def record(
        self,
        step_name: str,
        input_hashes: dict[str, str],
        params: dict,
        tool_versions: dict[str, str],
        output_hashes: dict[str, str],
        duration_seconds: float,
    ) -> None:
        self.data[step_name] = {
            "inputs": input_hashes,
            "params": params,
            "tool_versions": tool_versions,
            "outputs": output_hashes,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_seconds": round(duration_seconds, 3),
        }
        self.save()

    def invalidate(self, step_name: str) -> None:
        self.data.pop(step_name, None)
        self.save()

    def run_step(
        self,
        step_name: str,
        input_paths: list[Path],
        params: dict,
        output_paths: list[Path],
        tool_versions: dict[str, str],
        fn: Callable[[], None],
        force: bool = False,
    ) -> StepResult:
        """Ejecuta `fn` solo si hace falta. `fn` debe dejar todos los
        `output_paths` escritos en disco al terminar.
        """
        input_hashes = {str(p): sha256_file(p) for p in input_paths}

        if not force and self._is_up_to_date(step_name, input_hashes, params, output_paths):
            prev = self.data[step_name]
            return StepResult(ran=False, outputs={Path(k): v for k, v in prev["outputs"].items()})

        t0 = time.time()
        fn()
        duration = time.time() - t0

        missing = [p for p in output_paths if not p.exists()]
        if missing:
            raise RuntimeError(
                f"Paso '{step_name}' termino sin error pero no genero: "
                f"{', '.join(str(p) for p in missing)}"
            )

        output_hashes = {str(p): sha256_file(p) for p in output_paths}
        self.record(step_name, input_hashes, params, tool_versions, output_hashes, duration)
        return StepResult(ran=True, outputs={p: output_hashes[str(p)] for p in output_paths})
