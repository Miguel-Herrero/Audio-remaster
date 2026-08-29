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
