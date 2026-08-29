"""Convencion de carpetas de episodio — la que ya usas, sin cambios:

    <episodio>/
      00_sources/   inputs de video + audios extraidos + retimed
      01_stems/     separacion
      02_finals/    renders de REAPER
      <episodio>/   proyecto REAPER (mismo nombre que la carpeta raiz)
      .remaster/    NUEVO: manifest.json, align.json, align.png, logs/

El unico directorio nuevo es `.remaster/`, oculto, para no ensuciar las
carpetas de trabajo existentes.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


def fps_tag(fps: Fraction) -> str:
    """Fraction(24000, 1001) -> '23976', Fraction(25, 1) -> '25'.
    Mismo estilo de nombre que ya usas (`es_23976.flac`).
    """
    value = float(fps)
    if value == int(value):
        return str(int(value))
    return str(round(value, 3)).replace(".", "")


@dataclass(frozen=True)
class EpisodeLayout:
    root: Path

    @property
    def code(self) -> str:
        """s03e01 a partir de ~/Movies/s03e01."""
        return self.root.name

    @property
    def sources(self) -> Path:
        return self.root / "00_sources"

    @property
    def stems(self) -> Path:
        return self.root / "01_stems"

    @property
    def finals(self) -> Path:
        return self.root / "02_finals"

    @property
    def project_dir(self) -> Path:
        return self.root / self.code

    @property
    def state_dir(self) -> Path:
        return self.root / ".remaster"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"

    @property
    def align_json_path(self) -> Path:
        return self.state_dir / "align.json"

    @property
    def align_png_path(self) -> Path:
        return self.state_dir / "align.png"

    @property
    def notes_path(self) -> Path:
        return self.project_dir / "NOTES.md"

    def ensure_dirs(self) -> None:
        for d in (self.sources, self.stems, self.finals, self.project_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # --- rutas de salida con nombre fijo (00_sources) ---

    @property
    def es_source(self) -> Path:
        return self.sources / "es_source.flac"

    @property
    def en_source(self) -> Path:
        return self.sources / "en_source.flac"

    @property
    def ja_source(self) -> Path:
        return self.sources / "ja_source.flac"

    def es_retimed(self, target_fps: Fraction) -> Path:
        return self.sources / f"es_{fps_tag(target_fps)}.flac"

    # --- stems (01_stems) ---

    @property
    def es_vocals(self) -> Path:
        return self.stems / "es_vocals.flac"

    @property
    def es_instrumental(self) -> Path:
        return self.stems / "es_instrumental.flac"

    @property
    def en_vocals(self) -> Path:
        return self.stems / "en_vocals.flac"

    @property
    def en_instrumental(self) -> Path:
        return self.stems / "en_instrumental.flac"

    # --- proyecto REAPER (dentro de <ep>/) ---

    @property
    def rpp_edit(self) -> Path:
        return self.project_dir / f"{self.code}.RPP"

    @property
    def rpp_render(self) -> Path:
        return self.project_dir / f"{self.code}_render.RPP"

    # --- render final (02_finals) ---

    @property
    def es_reconstructed(self) -> Path:
        return self.finals / "es_reconstructed.flac"
