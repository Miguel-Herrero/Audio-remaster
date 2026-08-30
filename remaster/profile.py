"""Perfil de serie (profiles/sh1984.toml): nombres de pista, idiomas,
parametros de separacion y limites de loudness. Todo lo que antes vivia
hardcodeado en `steps/config.py` pasa aqui, para que el codigo no sepa
nada especifico de "Sherlock Holmes" — solo el perfil.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profiles" / "sh1984.toml"


@dataclass(frozen=True)
class TrackNames:
    video: str
    en_vox: str
    en_me: str
    es_vox: str
    es_me: str


@dataclass(frozen=True)
class Languages:
    es: str
    en: str
    ja: str


@dataclass(frozen=True)
class MuxConfig:
    es_track_name: str
    title_template: str  # p.ej. "Sherlock Holmes (1984) {code}"
    series_title: str  # para el nombre de fichero final, ver remaster/naming.py


@dataclass(frozen=True)
class LoudnessConfig:
    max_delta_db: float
    scene_min_silence_db: float
    scene_min_duration_s: float
    # Techo de pico sobre la señal cruda (ver steps/loudness.py, donde el
    # valor tiene su historia). Opcional en el TOML: sin la clave, -6.0.
    peak_ceiling_dbfs: float = -6.0
    # Umbral con el que `remaster verify` marca una VENTANA (nivel de
    # dialogo agregado sobre 2 min) como desviada respecto a EN VOX.
    verify_tolerance_db: float = 2.0
    # Umbral para un ITEM suelto, mucho mas alto a proposito. Comparar el
    # LUFS de un item de 2s de doblaje con el ingles del mismo instante
    # mide sobre todo que el fraseo no coincide: medido sobre un episodio
    # ya bien mezclado, la mediana de |delta| por item es 1.85 dB y el p90
    # 4.66 dB SIN que haya nada mal. Con 2 dB se marcaria casi la mitad de
    # los items y el informe no serviria de nada. 6 dB = por encima de la
    # mayor correccion que el propio pipeline puede aplicar
    # (`max_delta_db`), asi que lo que pase de ahi merece una escucha.
    verify_item_tolerance_db: float = 6.0


@dataclass(frozen=True)
class LocalSeparationConfig:
    model_filename: str
    sample_rate: int


@dataclass(frozen=True)
class MvsepSeparationConfig:
    sep_type: str
    add_opt1: str
    add_opt2: str
    output_format: str  # "5" = FLAC 24-bit lossless (verificado en mvsep.com/en/full_api)


@dataclass(frozen=True)
class ReaEqConfig:
    preset_name: str


@dataclass(frozen=True)
class Profile:
    name: str
    tracks: TrackNames
    languages: Languages
    mux: MuxConfig
    loudness: LoudnessConfig
    local_separation: LocalSeparationConfig
    mvsep_separation: MvsepSeparationConfig
    reaeq: ReaEqConfig


def load_profile(path: Path | None = None) -> Profile:
    path = path or DEFAULT_PROFILE_PATH
    with path.open("rb") as f:
        data = tomllib.load(f)

    return Profile(
        name=data.get("name", path.stem),
        tracks=TrackNames(**data["tracks"]),
        languages=Languages(**data["languages"]),
        mux=MuxConfig(**data["mux"]),
        loudness=LoudnessConfig(**data["loudness"]),
        local_separation=LocalSeparationConfig(**data["separation"]["local"]),
        mvsep_separation=MvsepSeparationConfig(**data["separation"]["mvsep"]),
        reaeq=ReaEqConfig(**data["reaeq"]),
    )
