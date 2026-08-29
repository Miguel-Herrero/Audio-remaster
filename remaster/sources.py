"""Clasificacion de fuentes en 00_sources/ por contenido, nunca por nombre
de fichero. Ver plan v2, seccion "Ingest".

Solo hay dos fuentes de audio, y son fijas:
  - DVD castellano (rip MakeMKV): aporta el audio `spa`.
  - Blu-ray: aporta el audio `eng` (y `jpn` si esta presente), el video de
    referencia, los subtitulos PGS, y define el frame rate destino.

El remaster 4K, si existe, es un upscale de YouTube: solo aporta video y su
subtitulo externo. Su audio (Opus, lossy) nunca se usa — no es una
heuristica de "preferir lossless", es un rol fijo (video_only).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .probe import ProbeResult, Stream, probe_file

VIDEO_CONTAINER_SUFFIXES = {".mkv", ".webm", ".mp4", ".mov", ".m2ts"}
SUBTITLE_SUFFIXES = {".vtt", ".srt"}
REMASTER_MIN_HEIGHT = 1440


class SourcesError(RuntimeError):
    """No se pudo clasificar de forma inequivoca el contenido de 00_sources/."""


@dataclass(frozen=True)
class AudioSource:
    path: Path
    stream_index: int
    language: str
    codec_name: str


@dataclass(frozen=True)
class VideoSource:
    path: Path
    stream_index: int
    width: int
    height: int
    frame_rate: Fraction
    codec_name: str = "unknown"


@dataclass(frozen=True)
class Remaster4K:
    video: VideoSource
    subtitle: Path | None


@dataclass(frozen=True)
class ClassifiedSources:
    dvd_container: Path
    dvd_video: VideoSource | None
    es: AudioSource
    bluray_container: Path
    bluray_video: VideoSource
    en: AudioSource
    ja: AudioSource | None
    remaster4k: Remaster4K | None
    target_fps: Fraction

    def summary_rows(self) -> list[tuple[str, str, str]]:
        """(rol, fichero, detalle) para pintar en la tabla de `remaster run`."""
        rows = [
            ("DVD (spa)", self.dvd_container.name,
             f"stream {self.es.stream_index}, {self.es.codec_name}"),
            ("Blu-ray (eng)", self.bluray_container.name,
             f"stream {self.en.stream_index}, {self.en.codec_name}"),
        ]
        if self.ja:
            rows.append(("Blu-ray (jpn)", self.bluray_container.name,
                          f"stream {self.ja.stream_index}, {self.ja.codec_name}"))
        else:
            rows.append(("Blu-ray (jpn)", "-", "no presente"))
        rows.append(("Frame rate destino", str(self.target_fps),
                      f"{float(self.target_fps):.3f} fps"))
        if self.remaster4k:
            sub = self.remaster4k.subtitle.name if self.remaster4k.subtitle else "-"
            rows.append(("Remaster 4K (solo video)", self.remaster4k.video.path.name,
                          f"{self.remaster4k.video.width}x{self.remaster4k.video.height}, subs: {sub}"))
        else:
            rows.append(("Remaster 4K", "-", "no presente (se omitira el remux 4K)"))
        return rows


@dataclass
class SourceOverrides:
    """Escotillas de escape para el caso raro. En el caso normal no se usa
    ninguna: la clasificacion automatica basta.
    """
    es_source: Path | None = None
    es_track: int | None = None
    en_source: Path | None = None
    en_track: int | None = None
    ja_track: int | None = None
    video_source: Path | None = None


def _video_containers(source_dir: Path) -> list[Path]:
    return sorted(
        p for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_CONTAINER_SUFFIXES
    )


def _find_external_subtitle(video_path: Path, source_dir: Path) -> Path | None:
    """El .vtt del 4K no comparte stem exacto con el .webm: comparten un
    prefijo comun ("... 4K AI Remaster") y el vtt anade ".en-GB.vtt".
    """
    video_stem = video_path.stem
    candidates = [
        p for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUBTITLE_SUFFIXES
        and p.name.startswith(video_stem)
    ]
    if not candidates:
        return None
    # El mas corto es el match mas directo si hay varios idiomas de subs.
    return sorted(candidates, key=lambda p: len(p.name))[0]


def _pick_spa_audio(probe: ProbeResult) -> Stream | None:
    for s in probe.audio:
        if s.language == "spa":
            return s
    return None


def _pick_lossless_audio(probe: ProbeResult, language: str) -> Stream | None:
    for s in probe.audio:
        if s.language == language and s.is_lossless:
            return s
    return None


def _looks_like_remaster_video(probe: ProbeResult) -> bool:
    return bool(probe.video) and (probe.video[0].height or 0) >= REMASTER_MIN_HEIGHT


def classify_sources(
    source_dir: Path,
    overrides: SourceOverrides | None = None,
    ffprobe_path: str = "ffprobe",
) -> ClassifiedSources:
    overrides = overrides or SourceOverrides()

    if not source_dir.is_dir():
        raise SourcesError(f"No existe la carpeta de fuentes: {source_dir}")

    containers = _video_containers(source_dir)
    if not containers:
        raise SourcesError(
            f"No hay ningun contenedor de video ({sorted(VIDEO_CONTAINER_SUFFIXES)}) "
            f"en {source_dir}"
        )

    probes: dict[Path, ProbeResult] = {}
    for c in containers:
        probes[c] = probe_file(c, ffprobe_path=ffprobe_path)

    # --- DVD castellano ---
    dvd_container: Path
    es_stream: Stream
    if overrides.es_source:
        dvd_container = overrides.es_source
        probes.setdefault(dvd_container, probe_file(dvd_container, ffprobe_path=ffprobe_path))
        p = probes[dvd_container]
        if overrides.es_track is not None:
            es_stream = next(s for s in p.audio if s.index == overrides.es_track)
        else:
            found = _pick_spa_audio(p)
            if not found:
                raise SourcesError(f"{dvd_container.name}: no tiene audio 'spa' y no se dio --es-track")
            es_stream = found
    else:
        dvd_candidates = [(c, _pick_spa_audio(probes[c])) for c in containers]
        dvd_candidates = [(c, s) for c, s in dvd_candidates if s is not None]
        if not dvd_candidates:
            examined = ", ".join(c.name for c in containers)
            raise SourcesError(
                f"No se encontro el DVD castellano: ningun fichero en {source_dir} "
                f"tiene una pista de audio 'spa'. Ficheros examinados: {examined}. "
                f"Usa --es-source/--es-track si el rip tiene un idioma sin etiquetar."
            )
        # Varios titulos de MakeMKV (*_t0*.mkv): gana el de mayor duracion.
        dvd_container, es_stream = max(dvd_candidates, key=lambda cs: probes[cs[0]].duration)

    # --- Blu-ray (referencia) ---
    bluray_container: Path
    en_stream: Stream
    ja_stream: Stream | None
    if overrides.en_source:
        bluray_container = overrides.en_source
        probes.setdefault(bluray_container, probe_file(bluray_container, ffprobe_path=ffprobe_path))
        p = probes[bluray_container]
        if overrides.en_track is not None:
            en_stream = next(s for s in p.audio if s.index == overrides.en_track)
        else:
            found = _pick_lossless_audio(p, "eng")
            if not found:
                raise SourcesError(f"{bluray_container.name}: no tiene audio 'eng' lossless y no se dio --en-track")
            en_stream = found
        if overrides.ja_track is not None:
            ja_stream = next(s for s in p.audio if s.index == overrides.ja_track)
        else:
            ja_stream = _pick_lossless_audio(p, "jpn")
    else:
        # El remaster 4K nunca aporta audio, ni siquiera si por error trajera
        # una pista 'eng' marcada como lossless: se excluye por rol (altura),
        # no por calidad de codec.
        bluray_candidates = [
            (c, _pick_lossless_audio(probes[c], "eng"))
            for c in containers
            if c != dvd_container and not _looks_like_remaster_video(probes[c])
        ]
        bluray_candidates = [(c, s) for c, s in bluray_candidates if s is not None]
        if not bluray_candidates:
            examined = ", ".join(c.name for c in containers)
            raise SourcesError(
                f"No se encontro el Blu-ray de referencia: ningun fichero en {source_dir} "
                f"tiene una pista de audio 'eng' en codec sin perdida (pcm/flac/truehd/mlp/alac). "
                f"Ficheros examinados: {examined}. Usa --en-source/--en-track si hace falta."
            )
        if len(bluray_candidates) > 1:
            names = ", ".join(c.name for c, _ in bluray_candidates)
            raise SourcesError(
                f"Varios ficheros parecen ser el Blu-ray de referencia ({names}). "
                f"Desambigua con --en-source."
            )
        bluray_container, en_stream = bluray_candidates[0]
        ja_stream = _pick_lossless_audio(probes[bluray_container], "jpn")

    bluray_probe = probes[bluray_container]
    bluray_video_stream = bluray_probe.video[0] if bluray_probe.video else None
    if bluray_video_stream is None or bluray_video_stream.frame_rate is None:
        raise SourcesError(f"{bluray_container.name}: no se pudo leer el frame rate de su video")

    # --- Remaster 4K (opcional, solo video) ---
    remaster4k: Remaster4K | None = None
    if overrides.video_source:
        v4k_path = overrides.video_source
        probes.setdefault(v4k_path, probe_file(v4k_path, ffprobe_path=ffprobe_path))
        vp = probes[v4k_path]
        if not vp.video:
            raise SourcesError(f"{v4k_path.name}: no tiene stream de video")
        vs = vp.video[0]
        remaster4k = Remaster4K(
            video=VideoSource(v4k_path, vs.index, vs.width or 0, vs.height or 0, vs.frame_rate or Fraction(0), vs.codec_name),
            subtitle=_find_external_subtitle(v4k_path, source_dir),
        )
    else:
        remaining = [c for c in containers if c not in (dvd_container, bluray_container)]
        v4k_candidates = [
            (c, probes[c].video[0]) for c in remaining
            if probes[c].video and (probes[c].video[0].height or 0) >= REMASTER_MIN_HEIGHT
        ]
        if len(v4k_candidates) > 1:
            names = ", ".join(c.name for c, _ in v4k_candidates)
            raise SourcesError(
                f"Varios ficheros parecen ser el remaster 4K ({names}). Desambigua con --video-source."
            )
        if v4k_candidates:
            v4k_path, vs = v4k_candidates[0]
            remaster4k = Remaster4K(
                video=VideoSource(v4k_path, vs.index, vs.width or 0, vs.height or 0, vs.frame_rate or Fraction(0), vs.codec_name),
                subtitle=_find_external_subtitle(v4k_path, source_dir),
            )

    dvd_probe = probes[dvd_container]
    dvd_video_stream = dvd_probe.video[0] if dvd_probe.video else None
    dvd_video = (
        VideoSource(dvd_container, dvd_video_stream.index,
                    dvd_video_stream.width or 0, dvd_video_stream.height or 0,
                    dvd_video_stream.frame_rate, dvd_video_stream.codec_name)
        if dvd_video_stream and dvd_video_stream.frame_rate else None
    )

    return ClassifiedSources(
        dvd_container=dvd_container,
        dvd_video=dvd_video,
        es=AudioSource(dvd_container, es_stream.index, es_stream.language or "spa", es_stream.codec_name),
        bluray_container=bluray_container,
        bluray_video=VideoSource(
            bluray_container, bluray_video_stream.index,
            bluray_video_stream.width or 0, bluray_video_stream.height or 0,
            bluray_video_stream.frame_rate, bluray_video_stream.codec_name,
        ),
        en=AudioSource(bluray_container, en_stream.index, en_stream.language or "eng", en_stream.codec_name),
        ja=(AudioSource(bluray_container, ja_stream.index, ja_stream.language or "jpn", ja_stream.codec_name)
            if ja_stream else None),
        remaster4k=remaster4k,
        target_fps=bluray_video_stream.frame_rate,
    )
