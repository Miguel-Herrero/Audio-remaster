"""Paso 4: separacion de stems. Backend intercambiable — `local` (por
defecto, offline) o `mvsep` (fallback/A-B). Ambos devuelven la misma
interfaz: (vocals_path, instrumental_path).

Politica de bits: se fuerza a 24-bit siempre, sea cual sea la profundidad
de la fuente. Los dos backends producen contenido nuevo derivado de un
modelo (no una copia de las muestras originales), asi que 24-bit evita
requantizar ese contenido nuevo — a diferencia de extract.py, donde
conservar la profundidad de la fuente ya basta (ver steps/extract.py).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests

from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult
from ..probe import probe_file, require_binary
from ..profile import LocalSeparationConfig, MvsepSeparationConfig
from ..toolinfo import ffmpeg_version


class SeparationError(RuntimeError):
    pass


class SeparatorBackend(Protocol):
    name: str

    def separate(self, input_path: Path, output_dir: Path) -> tuple[Path, Path]:
        """Devuelve (vocals_path, instrumental_path)."""
        ...

    def tool_version(self) -> str:
        ...


def _ensure_24bit_flac(input_path: Path, work_dir: Path, ffmpeg_path: str = "ffmpeg") -> Path:
    """Si `input_path` ya es 24-bit (o mas), lo devuelve tal cual. Si no,
    genera una copia temporal a 24-bit para que el separador reciba
    siempre la maxima precision disponible como entrada.
    """
    probe = probe_file(input_path)
    audio = probe.audio[0] if probe.audio else None
    if audio and (audio.bits_per_raw_sample or 0) >= 24:
        return input_path

    ffmpeg = require_binary("ffmpeg", ffmpeg_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    upsampled = work_dir / f"{input_path.stem}_24bit.flac"
    cmd = [ffmpeg, "-y", "-i", str(input_path), "-sample_fmt", "s32", "-c:a", "flac", str(upsampled)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SeparationError(f"No se pudo subir {input_path.name} a 24-bit:\n{result.stderr.strip()}")
    return upsampled


def _pick_by_keyword(paths: list[Path], keywords: tuple[str, ...]) -> Path | None:
    for p in paths:
        lowered = p.name.lower()
        if any(k in lowered for k in keywords):
            return p
    return None


@dataclass
class LocalSeparatorBackend:
    """`audio-separator` (BS-Roformer u otro modelo MDXC) sobre CPU/MPS.
    Offline, sin token, pesos cacheados localmente.
    """

    config: LocalSeparationConfig
    model_dir: Path
    name: str = "local"

    def tool_version(self) -> str:
        try:
            from importlib.metadata import PackageNotFoundError, version
            try:
                pkg_version = version("audio-separator")
            except PackageNotFoundError:
                pkg_version = "version desconocida"
            return f"audio-separator {pkg_version}, model={self.config.model_filename}"
        except ImportError:
            return f"audio-separator (no instalado), model={self.config.model_filename}"

    def separate(self, input_path: Path, output_dir: Path) -> tuple[Path, Path]:
        try:
            from audio_separator.separator import Separator
        except ImportError as exc:
            raise SeparationError(
                "El backend 'local' necesita el extra 'local-separation' "
                "(uv sync --extra local-separation --extra audio)."
            ) from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir = output_dir / "_24bit_in"
        source = _ensure_24bit_flac(input_path, work_dir)

        separator = Separator(
            output_dir=str(output_dir),
            output_format="FLAC",
            sample_rate=self.config.sample_rate,
            model_file_dir=str(self.model_dir),
        )
        separator.load_model(model_filename=self.config.model_filename)
        produced = [Path(p) for p in separator.separate(str(source))]

        vocals = _pick_by_keyword(produced, ("vocal",))
        instrumental = _pick_by_keyword(produced, ("instrumental", "other"))
        if not vocals or not instrumental:
            raise SeparationError(
                f"No se identificaron vocals/instrumental entre las salidas del modelo: "
                f"{[p.name for p in produced]}"
            )
        return vocals, instrumental


@dataclass
class MvsepSeparatorBackend:
    """Cliente HTTP de MVSEP. El token nunca se pasa por argv: viene de
    `MVSEP_API_TOKEN` o se inyecta explicitamente al construir el backend.
    """

    config: MvsepSeparationConfig
    api_token: str
    name: str = "mvsep"
    create_url: str = "https://mvsep.com/api/separation/create"
    check_url: str = "https://mvsep.com/api/separation/get"
    poll_interval_s: float = 5.0
    max_attempts: int = 120

    def tool_version(self) -> str:
        return f"mvsep sep_type={self.config.sep_type} add_opt1={self.config.add_opt1}"

    def _post_with_retries(self, input_path: Path) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with input_path.open("rb") as f:
                    response = requests.post(
                        self.create_url,
                        files={"audiofile": f},
                        data={
                            "api_token": self.api_token,
                            "sep_type": self.config.sep_type,
                            "add_opt1": self.config.add_opt1,
                            "add_opt2": self.config.add_opt2,
                            "output_format": self.config.output_format,
                            "is_demo": "0",
                        },
                        timeout=300,
                    )
                result = response.json()
                if not result.get("success"):
                    raise SeparationError(result.get("message", result.get("error", "error desconocido")))
                return result["data"]["hash"]
            except (requests.RequestException, SeparationError) as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise SeparationError(f"MVSEP: fallo subiendo {input_path.name} tras 3 intentos: {last_error}")

    def separate(self, input_path: Path, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        separation_hash = self._post_with_retries(input_path)

        for attempt in range(self.max_attempts):
            response = requests.get(
                self.check_url,
                params={"hash": separation_hash, "api_token": self.api_token},
                timeout=60,
            )
            result = response.json()
            if not result.get("success"):
                raise SeparationError(f"MVSEP: {result}")

            status = result.get("status", "unknown")
            if status == "done":
                files = result["data"].get("files", [])
                vocals_url = next((f["url"] for f in files if "vocal" in f.get("type", "").lower()), None)
                instrumental_url = next(
                    (f["url"] for f in files if any(k in f.get("type", "").lower() for k in ("other", "instrumental"))),
                    None,
                )
                if not vocals_url or not instrumental_url:
                    raise SeparationError(f"MVSEP: no se encontraron vocals/instrumental en {files}")

                vocals_path = output_dir / f"{input_path.stem}_vocals.flac"
                instrumental_path = output_dir / f"{input_path.stem}_instrumental.flac"
                vocals_path.write_bytes(requests.get(vocals_url, timeout=300).content)
                instrumental_path.write_bytes(requests.get(instrumental_url, timeout=300).content)
                return vocals_path, instrumental_path

            if status in ("failed", "error"):
                raise SeparationError(f"MVSEP: separacion fallida: {result}")

            time.sleep(self.poll_interval_s)

        raise SeparationError(f"MVSEP: timeout esperando la separacion de {input_path.name}")


@dataclass(frozen=True)
class SeparateOutcome:
    es: StepResult
    en: StepResult
    es_vocals: Path       # -> pista ES VOX
    es_instrumental: Path  # -> pista ES M&E
    en_vocals: Path        # -> pista EN VOX (referencia de sync/loudness)
    en_instrumental: Path  # -> pista EN M&E


def separate_all(
    es_input: Path,
    en_input: Path,
    layout: EpisodeLayout,
    manifest: Manifest,
    backend: SeparatorBackend,
    force: bool = False,
) -> SeparateOutcome:
    tool_versions = {"separator": backend.tool_version()}

    def _run_one(label: str, input_path: Path) -> tuple[StepResult, Path, Path]:
        out_dir = layout.stems / label
        vocals_marker = getattr(layout, f"{label}_vocals")
        instrumental_marker = getattr(layout, f"{label}_instrumental")
        params = {"backend": backend.name}

        def fn() -> None:
            vocals, instrumental = backend.separate(input_path, out_dir)
            vocals.replace(vocals_marker)
            instrumental.replace(instrumental_marker)

        result = manifest.run_step(
            step_name=f"separate:{label}",
            input_paths=[input_path],
            params=params,
            output_paths=[vocals_marker, instrumental_marker],
            tool_versions=tool_versions,
            fn=fn,
            force=force,
        )
        return result, vocals_marker, instrumental_marker

    es_result, es_vocals, es_instrumental = _run_one("es", es_input)
    en_result, en_vocals, en_instrumental = _run_one("en", en_input)

    return SeparateOutcome(
        es=es_result, en=en_result,
        es_vocals=es_vocals, es_instrumental=es_instrumental,
        en_vocals=en_vocals, en_instrumental=en_instrumental,
    )
