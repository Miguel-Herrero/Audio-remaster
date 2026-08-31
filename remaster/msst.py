"""Banco de pruebas de separacion sobre Music-Source-Separation-Training.

MSST (el repo de ZFTurbo) se usa **desde fuera**: se lee su catalogo de
configs, se ejecuta su `inference.py` con el interprete de su propio `venv`, y
no se escribe nunca dentro de su arbol. Asi puede quedarse en `main` y
actualizarse con `git pull` sin que nada de aqui se rompa.

Este modulo es logica pura: no importa FastAPI ni Typer, para poder probarlo
sin levantar nada.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

# Los dos unicos pares validos de flags de formato. `inference.py` decide la
# extension por el pico de la senal y el `pcm_type`, y las combinaciones
# intermedias no hacen lo que parece: `--pcm_type PCM_24` *sin* `--flac_file`
# ya escribe .flac, y `--flac_file --pcm_type FLOAT` avisa y cae a PCM_16.
FORMATS: dict[str, tuple[str, ...]] = {
    "flac24": ("--flac_file", "--pcm_type", "PCM_24"),
    "wav32": ("--pcm_type", "FLOAT"),
}

# `inference.py` solo mira dentro de `--input_folder` con un glob, asi que la
# carpeta que se le pase debe contener el audio y nada mas.
TEMPLATE = "{file_name}__{instr}"


class MsstError(RuntimeError):
    """Algo impide separar: falta el repo, el modelo o los pesos."""


# --------------------------------------------------------------------------
# Catalogo
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MsstVariant:
    """Una version del mismo modelo entrenada para un idioma concreto."""

    id: str
    label: str
    checkpoint: str
    checkpoint_url: Optional[str] = None
    needs_fix: bool = False
    size_mb: int = 0


@dataclass(frozen=True)
class MsstModel:
    id: str
    name: str
    model_type: str
    config: str
    checkpoint: str
    stems: tuple[str, ...]
    note: str = ""
    checkpoint_url: Optional[str] = None
    config_url: Optional[str] = None
    needs_fix: bool = False
    size_mb: int = 0
    # Algunos modelos no pueden correr en la GPU de Apple: la familia Bandit
    # registra buffers en float64 y Metal no soporta doble precision, asi que
    # `model.to("mps")` revienta con "Cannot convert a MPS Tensor to float64".
    # Se marcan aqui para forzarles CPU sin que haya que descubrirlo fallando.
    cpu_only: bool = False
    variants: tuple[MsstVariant, ...] = ()

    def variant(self, variant_id: Optional[str]) -> Optional[MsstVariant]:
        if not variant_id:
            return None
        for var in self.variants:
            if var.id == variant_id:
                return var
        raise MsstError(
            f"el modelo '{self.id}' no tiene la variante '{variant_id}'. "
            f"Tiene: {', '.join(v.id for v in self.variants) or 'ninguna'}"
        )

    def checkpoint_ref(self, variant: Optional[MsstVariant]) -> str:
        return variant.checkpoint if variant else self.checkpoint

    def slug(self, variant: Optional[MsstVariant]) -> str:
        """Lo que se cuela en el nombre del fichero de salida."""
        return f"{self.id}_{variant.id}" if variant else self.id


DEFAULT_CATALOG = Path(__file__).parent / "msst_models.toml"


def _variant_from(raw: dict) -> MsstVariant:
    return MsstVariant(
        id=raw["id"],
        label=raw.get("label", raw["id"]),
        checkpoint=raw["checkpoint"],
        checkpoint_url=raw.get("checkpoint_url"),
        needs_fix=bool(raw.get("needs_fix", False)),
        size_mb=int(raw.get("size_mb", 0)),
    )


def _model_from(raw: dict) -> MsstModel:
    return MsstModel(
        id=raw["id"],
        name=raw["name"],
        model_type=raw["model_type"],
        config=raw["config"],
        checkpoint=raw["checkpoint"],
        stems=tuple(raw.get("stems", ())),
        note=raw.get("note", ""),
        checkpoint_url=raw.get("checkpoint_url"),
        config_url=raw.get("config_url"),
        needs_fix=bool(raw.get("needs_fix", False)),
        size_mb=int(raw.get("size_mb", 0)),
        cpu_only=bool(raw.get("cpu_only", False)),
        variants=tuple(_variant_from(v) for v in raw.get("variant", ())),
    )


def load_catalog(
    base: Path = DEFAULT_CATALOG, extra: Optional[Path] = None
) -> list[MsstModel]:
    """Catalogo base, con `extra` fusionado encima por `id`.

    El fichero extra permite anadir o redefinir modelos sin tocar el codigo ni
    reinstalar nada: es lo que hace configurable el catalogo.
    """
    models: dict[str, MsstModel] = {}
    for path in (base, extra):
        if path is None or not path.is_file():
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for raw in data.get("model", ()):
            model = _model_from(raw)
            models[model.id] = model
    return list(models.values())


def find_model(models: list[MsstModel], model_id: str) -> MsstModel:
    for model in models:
        if model.id == model_id:
            return model
    raise MsstError(
        f"modelo desconocido: '{model_id}'. "
        f"Disponibles: {', '.join(m.id for m in models)}"
    )


# --------------------------------------------------------------------------
# El repo clonado
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MsstRepo:
    """Un clon de Music-Source-Separation-Training, en solo lectura."""

    root: Path
    data_dir: Path

    def python_bin(self) -> Path:
        return self.root / "venv" / "bin" / "python"

    def inference_py(self) -> Path:
        return self.root / "inference.py"

    def problems(self) -> list[str]:
        """Diagnostico legible. Vacio si se puede separar."""
        out: list[str] = []
        if not self.root.is_dir():
            out.append(f"no existe la carpeta del repo: {self.root}")
            return out
        if not self.inference_py().is_file():
            out.append(f"falta {self.inference_py()} — ¿es el repo correcto?")
        if not self.python_bin().is_file():
            out.append(
                f"falta el interprete {self.python_bin()}. Crea el entorno del repo:"
                " python3 -m venv venv && venv/bin/pip install -r requirements.txt"
            )
        return out

    def resolve(self, ref: str) -> Path:
        """`repo:x` cuelga del clon; `data:x` de nuestra carpeta de datos.

        Los dos prefijos existen para que el catalogo distinga lo que ya trae
        el repo ajeno (sus configs) de lo que descargamos nosotros, que nunca
        se escribe dentro de el.
        """
        if ref.startswith("repo:"):
            return self.root / ref[5:]
        if ref.startswith("data:"):
            return self.data_dir / ref[5:]
        path = Path(ref).expanduser()
        if not path.is_absolute():
            raise MsstError(
                f"referencia sin prefijo ni ruta absoluta: {ref!r} "
                "(usa 'repo:', 'data:' o una ruta absoluta)"
            )
        return path

    def status(
        self, model: MsstModel, variant: Optional[MsstVariant] = None
    ) -> dict:
        config = self.resolve(model.config)
        checkpoint = self.resolve(model.checkpoint_ref(variant))
        return {
            "config_ok": config.is_file(),
            "checkpoint_ok": checkpoint.is_file(),
            "config_path": str(config),
            "checkpoint_path": str(checkpoint),
        }


def build_command(
    repo: MsstRepo,
    model: MsstModel,
    input_dir: Path,
    store_dir: Path,
    *,
    variant: Optional[MsstVariant] = None,
    fmt: str = "flac24",
    extract_instrumental: bool = False,
    use_tta: bool = False,
    force_cpu: bool = False,
) -> list[str]:
    """El argv exacto con que se invoca `inference.py`."""
    if fmt not in FORMATS:
        raise MsstError(
            f"formato desconocido: {fmt!r}. Usa uno de: {', '.join(FORMATS)}"
        )

    argv = [
        str(repo.python_bin()),
        str(repo.inference_py()),
        "--model_type", model.model_type,
        "--config_path", str(repo.resolve(model.config)),
        "--start_check_point", str(repo.resolve(model.checkpoint_ref(variant))),
        "--input_folder", str(input_dir),
        "--store_dir", str(store_dir),
        "--filename_template", TEMPLATE,
    ]
    argv += list(FORMATS[fmt])
    if model.cpu_only:
        force_cpu = True
    if extract_instrumental:
        argv.append("--extract_instrumental")
    if use_tta:
        argv.append("--use_tta")
    if force_cpu:
        argv.append("--force_cpu")
    return argv


# --------------------------------------------------------------------------
# Pesos de PyTorch Lightning
# --------------------------------------------------------------------------

def fix_lightning_checkpoint(src: Path, dst: Path) -> int:
    """Convierte un checkpoint de Lightning en el state dict que MSST espera.

    Los pesos publicados de Bandit v2 (Zenodo 12701995) son checkpoints de
    Lightning enteros. El cargador de MSST desenvuelve `state_dict` pero no
    quita el prefijo `model.` de las claves ni descarta las de la funcion de
    perdida, y su `load_state_dict` es estricto: sin esta conversion aborta con
    "Unexpected key(s) in state_dict". La receta es la que da el autor de MSST
    en https://github.com/ZFTurbo/Music-Source-Separation-Training/issues/41

    Devuelve el numero de claves del resultado.
    """
    import torch  # diferido: solo hace falta al descargar, no al listar

    raw = torch.load(src, map_location="cpu", weights_only=False)
    weights = raw.get("state_dict", raw) if isinstance(raw, dict) else raw

    for key in list(weights):
        if key.startswith("model."):
            weights[key[len("model."):]] = weights.pop(key)
        elif key.startswith("loss_handler"):
            weights.pop(key, None)

    dst.parent.mkdir(parents=True, exist_ok=True)
    torch.save(weights, dst)
    return len(weights)


def catalog_with_status(
    models: list[MsstModel], repo: MsstRepo
) -> list[dict]:
    """El catalogo en forma serializable, con lo que hay en disco."""
    out: list[dict] = []
    for model in models:
        entry = {
            "id": model.id,
            "name": model.name,
            "model_type": model.model_type,
            "stems": list(model.stems),
            "note": model.note,
            "size_mb": model.size_mb,
            "cpu_only": model.cpu_only,
            "downloadable": bool(model.checkpoint_url),
            **repo.status(model),
            "variants": [],
        }
        for var in model.variants:
            status = repo.status(model, var)
            entry["variants"].append({
                "id": var.id,
                "label": var.label,
                "size_mb": var.size_mb,
                "downloadable": bool(var.checkpoint_url),
                "checkpoint_ok": status["checkpoint_ok"],
                "checkpoint_path": status["checkpoint_path"],
            })
        entry["ready"] = entry["config_ok"] and (
            entry["checkpoint_ok"]
            or any(v["checkpoint_ok"] for v in entry["variants"])
        )
        out.append(entry)
    return out


__all__ = [
    "FORMATS",
    "MsstError",
    "MsstModel",
    "MsstRepo",
    "MsstVariant",
    "build_command",
    "catalog_with_status",
    "find_model",
    "fix_lightning_checkpoint",
    "load_catalog",
    "replace",
]
