"""CLI del pipeline: `remaster doctor|sources|run|status|notes`.

Un comando, una carpeta de episodio con el layout que ya usas
(00_sources/, 01_stems/, 02_finals/, <episodio>/).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .doctor import run_all as doctor_run_all
from .events import EventLog
from .layout import EpisodeLayout
from .manifest import Manifest
from .naming import build_final_filename
from .notes import write_notes
from .probe import probe_file
from .profile import Profile, load_profile
from .reaper.render_stats import newest_render_stats
from .sources import ClassifiedSources, SourceOverrides, SourcesError, classify_sources
from .steps.align import CalibrationPoint, CalibrationResult, calibrate_from_points, manual_align, run_align
from .steps.extract import extract_all
from .steps.loudness import loudness_step
from .steps.mux import mux_step, subtitle_languages
from .steps.project import ProjectEditedError, build_project_step
from .steps.remux4k import remux4k_step
from .steps.render import REAPER_BINARY, render_step
from .steps.retime import retime_step
from .steps.verify import STATUS_DOUBLE_GAIN, VerifyError, build_actions, format_timecode, verify_project, write_report
from .steps.separate import LocalSeparatorBackend, MvsepSeparatorBackend, SeparatorBackend, separate_all
from .steps_meta import step_keys
from .msst import (
    FORMATS,
    MsstError,
    MsstModel,
    MsstRepo,
    build_command,
    find_model,
    fix_lightning_checkpoint,
    load_catalog,
)
from .web.envfile import read_env

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

# El orden sale de steps_meta.py, que es donde vive tambien la
# descripcion de cada paso: asi no se puede añadir uno aqui y olvidarse
# de explicar que hace.
STEP_ORDER = step_keys()

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "remaster" / "models"
DEFAULT_MSST_DATA_DIR = Path.home() / ".cache" / "remaster" / "msst"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@app.command()
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Proponer comandos de instalacion (pide confirmacion, no instala solo)"),
    mkvmerge_path: Optional[str] = typer.Option(None, help="Ruta explicita a mkvmerge"),
    mkvextract_path: Optional[str] = typer.Option(None, help="Ruta explicita a mkvextract"),
) -> None:
    """Comprueba las herramientas externas necesarias."""
    checks = doctor_run_all(mkvmerge_path=mkvmerge_path, mkvextract_path=mkvextract_path)

    table = Table(title="remaster doctor")
    table.add_column("Herramienta")
    table.add_column("Estado")
    table.add_column("Detalle")
    for c in checks:
        status = "[green]OK[/green]" if c.ok else "[red]FALTA[/red]"
        table.add_row(c.name, status, c.detail)
    console.print(table)

    missing = [c for c in checks if not c.ok]
    if not missing:
        console.print("[green]Todo listo.[/green]")
        raise typer.Exit(0)

    console.print("\n[yellow]Pendiente:[/yellow]")
    for c in missing:
        console.print(f"  - {c.name}: {c.fix_hint or 'sin sugerencia automatica'}")

    if fix:
        for c in missing:
            if not c.fix_hint or c.fix_hint.startswith("Descargar"):
                continue
            if typer.confirm(f"Ejecutar: {c.fix_hint} ?", default=False):
                import subprocess
                subprocess.run(c.fix_hint, shell=True, check=False)

    raise typer.Exit(1)


def _build_overrides(
    es_source: Optional[Path], es_track: Optional[int],
    en_source: Optional[Path], en_track: Optional[int],
    ja_track: Optional[int], video_source: Optional[Path],
) -> SourceOverrides:
    return SourceOverrides(
        es_source=es_source, es_track=es_track,
        en_source=en_source, en_track=en_track,
        ja_track=ja_track, video_source=video_source,
    )


def _print_sources_table(sources: ClassifiedSources) -> None:
    table = Table(title="Fuentes detectadas")
    table.add_column("Rol")
    table.add_column("Fichero")
    table.add_column("Detalle")
    for role, filename, detail in sources.summary_rows():
        table.add_row(role, filename, detail)
    console.print(table)


def _begin(step: str, events: EventLog, suffix: str = "") -> None:
    """Cabecera del paso, en la consola y en la traza a la vez."""
    console.print(f"\n[bold]{step}[/bold]{suffix}")
    events.start(step)


def _finish(events: EventLog) -> None:
    events.close()
    console.print("\n[green]Listo.[/green]")


def _run_verify_in_pipeline(layout: EpisodeLayout, profile: Profile) -> str | None:
    """`verify` dentro de un `run`. Devuelve el motivo si se omite.

    Version corta: escribe el informe y enumera los titulares. El detalle
    (tablas de global, envolvente, items) se queda para `remaster verify`,
    que es donde vas cuando quieres mirarlo de verdad.
    """
    if not layout.rpp_edit.exists():
        return "todavia no hay proyecto que revisar"

    stats_path = newest_render_stats(stats_search_dirs(layout))
    if stats_path is None:
        return (
            "sin medida de REAPER — abre el proyecto, selecciona ES VOX y EN VOX, ejecuta "
            "«Calculate loudness of selected tracks via dry run render» y repite con --from verify --to verify"
        )

    try:
        report = verify_project(layout, profile, stats_path)
    except VerifyError as exc:
        return str(exc)

    actions = build_actions(report)
    if not actions:
        console.print("  [green]nada que revisar: el castellano cuadra con el ingles[/green]")
    for n, action in enumerate(actions, start=1):
        colour = {"alta": "red", "media": "yellow"}.get(action.urgency, "cyan")
        console.print(f"  [{colour}]{n}. {action.title}[/{colour}]", highlight=False)

    _, html_path = write_report(layout, report)
    console.print(f"  informe: [dim]{html_path}[/dim]")
    return None


def _report_step(label: str, result, events: EventLog | None = None) -> None:
    if result is None:
        console.print(f"  {label}: [dim]omitido[/dim]")
        outcome = "skipped"
    elif result.ran:
        console.print(f"  {label}: [yellow]ejecutado[/yellow]")
        outcome = "ran"
    else:
        console.print(f"  {label}: [dim]sin cambios (no-op)[/dim]")
        outcome = "noop"
    if events is not None:
        events.note(outcome)


@app.command()
def sources(
    episode: Path = typer.Argument(..., help="Carpeta del episodio, p.ej. ~/Movies/s03e01"),
    es_source: Optional[Path] = typer.Option(None, help="Forzar fichero fuente del DVD castellano"),
    es_track: Optional[int] = typer.Option(None, help="Forzar indice de stream del audio castellano"),
    en_source: Optional[Path] = typer.Option(None, help="Forzar fichero fuente del Blu-ray"),
    en_track: Optional[int] = typer.Option(None, help="Forzar indice de stream del audio ingles"),
    ja_track: Optional[int] = typer.Option(None, help="Forzar indice de stream del audio japones"),
    video_source: Optional[Path] = typer.Option(None, help="Forzar fichero del remaster 4K"),
) -> None:
    """Clasifica 00_sources/ y muestra que ha detectado, sin tocar nada."""
    layout = EpisodeLayout(root=episode.expanduser().resolve())
    overrides = _build_overrides(es_source, es_track, en_source, en_track, ja_track, video_source)
    try:
        classified = classify_sources(layout.sources, overrides=overrides)
    except SourcesError as exc:
        console.print(f"[red]Error clasificando fuentes:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_sources_table(classified)


def _validate_range(from_step: str, to_step: str) -> tuple[int, int]:
    if from_step not in STEP_ORDER:
        raise typer.BadParameter(f"--from debe ser uno de {STEP_ORDER}")
    if to_step not in STEP_ORDER:
        raise typer.BadParameter(f"--to debe ser uno de {STEP_ORDER}")
    i, j = STEP_ORDER.index(from_step), STEP_ORDER.index(to_step)
    if i > j:
        raise typer.BadParameter(f"--from ({from_step}) va despues de --to ({to_step})")
    return i, j


def _build_separator_backend(
    separator: str, profile: Profile, mvsep_token: Optional[str], model_dir: Path,
) -> SeparatorBackend:
    if separator == "local":
        return LocalSeparatorBackend(config=profile.local_separation, model_dir=model_dir)
    if separator == "mvsep":
        token = mvsep_token or os.environ.get("MVSEP_API_TOKEN")
        if not token:
            console.print(
                "[red]Falta el token de MVSEP.[/red] Pasa --mvsep-token o define MVSEP_API_TOKEN "
                "(nunca lo pongas en un script versionado: se filtra por el historial de shell)."
            )
            raise typer.Exit(1)
        return MvsepSeparatorBackend(config=profile.mvsep_separation, api_token=token)
    raise typer.BadParameter("--separator debe ser 'local' o 'mvsep'")


@app.command()
def run(
    episode: Path = typer.Argument(..., help="Carpeta del episodio, p.ej. ~/Movies/s03e01"),
    from_step: str = typer.Option("ingest", "--from", help=f"Paso inicial: {STEP_ORDER}"),
    to_step: str = typer.Option("mux", "--to", help=f"Paso final: {STEP_ORDER}"),
    force: bool = typer.Option(False, "--force", help="Re-ejecutar aunque el manifiesto diga que esta al dia"),
    profile_path: Optional[Path] = typer.Option(None, "--profile", help="Ruta a un perfil TOML (por defecto profiles/sh1984.toml)"),
    events_path: Optional[Path] = typer.Option(
        None, "--events",
        help="Escribir la traza de pasos en NDJSON en este fichero (lo usa la UI para dibujar el progreso)",
    ),
    ffmpeg_path: str = typer.Option("ffmpeg", help="Ruta a ffmpeg"),
    mkvmerge_path: str = typer.Option("mkvmerge", help="Ruta a mkvmerge"),
    reaper_binary: str = typer.Option(REAPER_BINARY, help="Ruta al binario de REAPER"),
    separator: str = typer.Option("local", help="Backend de separacion: local | mvsep"),
    mvsep_token: Optional[str] = typer.Option(None, help="Token de MVSEP (mejor usar MVSEP_API_TOKEN)"),
    model_dir: Path = typer.Option(DEFAULT_MODEL_DIR, help="Directorio de pesos del separador local"),
    title: Optional[str] = typer.Option(None, help="Titulo (metadato interno del MKV); por defecto, el de --name o el del Blu-ray"),
    final_name: Optional[str] = typer.Option(
        None, "--name",
        help='Nombre completo del fichero final hasta el guion, tal cual y sin interpolar nada '
             '(p.ej. "Las aventuras de Sherlock Holmes (1984) S02E06 El problema final"). Queda '
             '"<name> - [<res>p <codec>] [FLAC ...] [Subs ...].mkv". Sin esto se usa el nombre del Blu-ray tal cual.',
    ),
    out_dir: Optional[Path] = typer.Option(None, "--out-dir", help="Carpeta de salida del MKV final (por defecto, la raiz del episodio)"),
    es_source: Optional[Path] = typer.Option(None, help="Forzar fichero fuente del DVD castellano"),
    es_track: Optional[int] = typer.Option(None, help="Forzar indice de stream del audio castellano"),
    en_source: Optional[Path] = typer.Option(None, help="Forzar fichero fuente del Blu-ray"),
    en_track: Optional[int] = typer.Option(None, help="Forzar indice de stream del audio ingles"),
    ja_track: Optional[int] = typer.Option(None, help="Forzar indice de stream del audio japones"),
    video_source: Optional[Path] = typer.Option(None, help="Forzar fichero del remaster 4K"),
    sync_offset: Optional[float] = typer.Option(
        None, "--sync-offset",
        help="Calibracion manual del sync (segundos): sustituye el sync automatico. Requiere --sync-drift.",
    ),
    sync_drift: Optional[float] = typer.Option(
        None, "--sync-drift",
        help="Deriva manual del sync (s/s): sustituye el sync automatico. Requiere --sync-offset.",
    ),
) -> None:
    """Ejecuta el pipeline sobre una carpeta de episodio, de punta a punta
    o por tramos con --from/--to.
    """
    if (sync_offset is None) != (sync_drift is None):
        console.print("[red]--sync-offset y --sync-drift van juntos: pasa los dos o ninguno.[/red]")
        raise typer.Exit(2)
    lo, hi = _validate_range(from_step, to_step)

    def wants(step: str) -> bool:
        return STEP_ORDER.index(step) <= hi

    def runs(step: str) -> bool:
        """True si este paso se invoca de verdad (dentro de [--from,--to]).
        Un paso anterior a --from no se re-ejecuta ni se hashea: se asume
        hecho y se localiza por convencion de nombre (ver _expect en cada
        bloque). Esto es lo que hace que --from sea rapido de verdad, no
        solo un no-op de manifiesto sobre ficheros de varios GB.
        """
        return lo <= STEP_ORDER.index(step) <= hi

    def _expect(path: Path, step_hint: str) -> Path:
        if not path.exists():
            console.print(
                f"[red]Falta {path}[/red] — necesario para continuar desde '{from_step}'. "
                f"Ejecuta antes 'remaster run {episode} --to {step_hint}'."
            )
            raise typer.Exit(1)
        return path

    layout = EpisodeLayout(root=episode.expanduser().resolve())
    layout.ensure_dirs()
    manifest = Manifest(layout.manifest_path)
    profile = load_profile(profile_path)
    events = EventLog(events_path)

    overrides = _build_overrides(es_source, es_track, en_source, en_track, ja_track, video_source)
    try:
        classified = classify_sources(layout.sources, overrides=overrides)
    except SourcesError as exc:
        console.print(f"[red]Error clasificando fuentes:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[bold]{layout.code}[/bold]")
    _print_sources_table(classified)

    if hi == STEP_ORDER.index("ingest"):
        _finish(events)
        return

    # --- extract ---
    if runs("extract"):
        _begin("extract", events)
        outcome = extract_all(classified, layout, manifest, ffmpeg_path=ffmpeg_path, force=force)
        _report_step("es", outcome.es, events)
        _report_step("en", outcome.en, events)
        _report_step("ja", outcome.ja, events)
    elif wants("retime") or wants("separate"):
        _expect(layout.es_source, "extract")
        _expect(layout.en_source, "extract")

    if hi == STEP_ORDER.index("extract"):
        _finish(events)
        return

    # --- retime ---
    needs_retime = classified.dvd_video is not None and classified.dvd_video.frame_rate != classified.target_fps
    if runs("retime"):
        _begin("retime", events)
        es_for_separation, retime_result = retime_step(classified, layout, manifest, ffmpeg_path=ffmpeg_path, force=force)
        if retime_result is None:
            console.print(f"  DVD ya esta a {classified.target_fps} fps, no hace falta retimear.")
        else:
            _report_step(es_for_separation.name, retime_result, events)
    else:
        es_for_separation = layout.es_retimed(classified.target_fps) if needs_retime else layout.es_source
        if wants("separate"):
            _expect(es_for_separation, "retime")

    if hi == STEP_ORDER.index("retime"):
        _finish(events)
        return

    # --- separate ---
    if runs("separate"):
        _begin("separate", events, suffix=f" (backend={separator})")
        backend = _build_separator_backend(separator, profile, mvsep_token, model_dir)
        sep_outcome = separate_all(es_for_separation, layout.en_source, layout, manifest, backend, force=force)
        _report_step("es (vocals+instrumental)", sep_outcome.es, events)
        _report_step("en (vocals+instrumental)", sep_outcome.en, events)
        es_vocals, es_instrumental = sep_outcome.es_vocals, sep_outcome.es_instrumental
        en_vocals, en_instrumental = sep_outcome.en_vocals, sep_outcome.en_instrumental
    else:
        es_vocals = _expect(layout.es_vocals, "separate")
        es_instrumental = _expect(layout.es_instrumental, "separate")
        en_vocals = _expect(layout.en_vocals, "separate")
        en_instrumental = _expect(layout.en_instrumental, "separate")

    if hi == STEP_ORDER.index("separate"):
        _finish(events)
        return

    # --- align ---
    if runs("align"):
        _begin("align", events)
        if sync_offset is not None:
            from .steps.align import manual_align
            align_result, align_step_result = manual_align(sync_offset, sync_drift, layout, manifest, force=force)
            _report_step("sync (calibracion manual)", align_step_result, events)
            console.print(f"  offset={align_result.offset_seconds:+.3f}s  drift={align_result.drift_slope:+.5f}s/s")
        else:
            align_result, align_step_result = run_align(es_vocals, en_vocals, layout, manifest, force=force)
            _report_step("sync", align_step_result, events)
            console.print(
                f"  offset={align_result.offset_seconds:+.3f}s  drift={align_result.drift_slope:+.5f}s/s  "
                f"confianza={align_result.confidence:.2f}"
            )
            if align_result.low_confidence:
                console.print(
                    "  [yellow]confianza baja — revisa .remaster/align.png antes de confiar en el sync automatico[/yellow]"
                )
    else:
        _expect(layout.align_json_path, "align")
        import json as _json
        align_data = _json.loads(layout.align_json_path.read_text())
        from .steps.align import AlignResult as _AlignResult
        align_result = _AlignResult(
            align_data["offset_seconds"], align_data["drift_slope"],
            align_data["confidence"], align_data["low_confidence"],
        )

    if hi == STEP_ORDER.index("align"):
        _finish(events)
        return

    # --- loudness ---
    if runs("loudness"):
        _begin("loudness", events)
        loudness_result, loudness_step_result = loudness_step(
            es_vocals, en_vocals, en_instrumental, layout, manifest, profile.loudness, align_result, force=force,
        )
        _report_step("lufs", loudness_step_result, events)
        console.print(
            f"  ganancia global={loudness_result.global_gain_db:+.2f}dB  "
            f"puntos de escena={len(loudness_result.scene_vol_points)}"
        )
    else:
        cache_path = layout.state_dir / "loudness.json"
        _expect(cache_path, "loudness")
        import json as _json
        from .reaper.rpp import VolPoint as _VolPoint
        from .steps.loudness import LoudnessResult as _LoudnessResult
        data = _json.loads(cache_path.read_text())
        loudness_result = _LoudnessResult(
            data["global_gain_db"],
            tuple(_VolPoint(p["time"], p["gain"], p["shape"]) for p in data["scene_vol_points"]),
        )

    if hi == STEP_ORDER.index("loudness"):
        _finish(events)
        return

    # --- project ---
    if runs("project"):
        _begin("project", events)
        try:
            project_result = build_project_step(
                classified, layout, profile,
                en_vocals, en_instrumental, es_vocals, es_instrumental,
                align_result, loudness_result, manifest, force=force,
            )
        except ProjectEditedError as exc:
            console.print(f"\n[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        _report_step(layout.rpp_edit.name, project_result, events)
        from .steps.project import MIN_TOTAL_DRIFT_TO_SEGMENT_S
        total_drift = abs(align_result.drift_slope) * probe_file(en_vocals).duration
        if total_drift >= MIN_TOTAL_DRIFT_TO_SEGMENT_S:
            console.print(
                f"  drift corregido con cortes en silencios de ES VOX (sin estirar el audio), "
                f"~{total_drift:.2f}s acumulados en todo el episodio"
            )
    else:
        _expect(layout.rpp_edit, "project")

    if hi == STEP_ORDER.index("project"):
        _finish(events)
        return

    # --- verify ---
    # Va aqui porque es donde de verdad cae: entre generar el proyecto y
    # renderizarlo esta el rato en el que lo editas a mano, y esto revisa
    # justo eso. Nunca corta el pipeline: es un informe, no una puerta.
    # Sin medida de REAPER se omite en vez de dar un analisis a medias
    # — el analisis parcial sigue disponible en `remaster verify --no-stats`,
    # pero pedirlo tiene que ser una decision, no el silencio por defecto.
    if runs("verify"):
        _begin("verify", events)
        reason = _run_verify_in_pipeline(layout, profile)
        if reason:
            console.print(f"  [dim]omitido — {reason}[/dim]")
            events.close(outcome="skipped", reason=reason)
        else:
            events.note("ran")

    if hi == STEP_ORDER.index("verify"):
        _finish(events)
        return

    # --- render ---
    if runs("render"):
        _begin("render", events)
        expected_duration = probe_file(en_vocals).duration
        render_result = render_step(
            layout, expected_duration, manifest, profile, reaper_binary=reaper_binary, force=force)
        _report_step(layout.es_reconstructed.name, render_result, events)
    else:
        _expect(layout.es_reconstructed, "render")

    if hi == STEP_ORDER.index("render"):
        _finish(events)
        return

    # --- mux ---
    out_dir_resolved = (out_dir or layout.root).expanduser().resolve()
    audio_langs = ["eng", "spa"] + (["jpn"] if classified.ja else [])

    if final_name:
        sub_langs = subtitle_languages(classified.bluray_container, mkvmerge_path)
        output_filename = build_final_filename(
            final_name,
            classified.bluray_video.height, classified.bluray_video.codec_name,
            audio_langs, sub_langs,
        )
    else:
        output_filename = classified.bluray_container.name
    output_file = out_dir_resolved / output_filename

    if runs("mux"):
        _begin("mux", events)
        mux_title = title
        if mux_title is None:
            if final_name:
                mux_title = final_name
            else:
                from .steps.remux4k import container_title
                mux_title = container_title(classified.bluray_container, mkvmerge_path) or profile.mux.title_template.format(code=layout.code)

        ja_flac = layout.ja_source if classified.ja else None
        mux_result = mux_step(
            classified.bluray_container, layout.en_source, layout.es_reconstructed, ja_flac,
            output_file, mux_title, profile, manifest, mkvmerge_path=mkvmerge_path, force=force,
        )
        _report_step(output_file.name, mux_result, events)
    else:
        _expect(output_file, "mux")

    if hi == STEP_ORDER.index("mux"):
        _finish(events)
        return

    # --- remux4k (opcional) ---
    if not classified.remaster4k:
        console.print("\n[bold]remux4k[/bold]")
        console.print("  [dim]sin fuente 4K en 00_sources — omitido[/dim]")
        events.skip("remux4k", "sin fuente 4K en 00_sources")
        _finish(events)
        return

    _begin("remux4k", events)
    if final_name:
        output_4k = out_dir_resolved / build_final_filename(
            final_name,
            classified.remaster4k.video.height, classified.remaster4k.video.codec_name,
            audio_langs, ["eng"],
        )
    else:
        output_4k = output_file.with_name(f"{output_file.stem} [4K]{output_file.suffix}")
    remux_result = remux4k_step(
        output_file, classified.remaster4k.video.path, classified.remaster4k.video.stream_index,
        classified.remaster4k.subtitle, output_4k, manifest, mkvmerge_path=mkvmerge_path, force=force,
    )
    _report_step(output_4k.name, remux_result, events)

    _finish(events)


def run_calibration(
    episode: Path,
    es_times: list[float],
    en_times: list[float],
    against_offset: float = 0.0,
    apply: bool = False,
    force: bool = False,
):
    """Logica compartida entre `remaster calibrate` y la UI web
    (POST /api/calibrate) — un solo camino de codigo, ver remaster/web/app.py.
    """
    if len(es_times) != len(en_times):
        raise ValueError("--es-time y --en-time deben tener el mismo numero de valores, en el mismo orden")
    if len(es_times) < 2:
        raise ValueError("Hacen falta al menos 2 puntos para calibrar")

    points = [CalibrationPoint(es, en) for es, en in zip(es_times, en_times)]
    result = calibrate_from_points(points, against_offset=against_offset)

    step_ran = None
    if apply:
        layout = EpisodeLayout(root=episode.expanduser().resolve())
        layout.ensure_dirs()
        manifest = Manifest(layout.manifest_path)
        _, step_result = manual_align(result.offset_seconds, result.drift_slope, layout, manifest, force=force)
        step_ran = step_result.ran

    return result, step_ran


@app.command()
def calibrate(
    episode: Path = typer.Argument(..., help="Carpeta del episodio"),
    es_time: list[float] = typer.Option(..., help="Timestamp en ES (segundos). Repetir en el mismo orden que --en-time; minimo 2."),
    en_time: list[float] = typer.Option(..., help="Timestamp en EN (segundos) correspondiente. Mismo orden que --es-time."),
    against_offset: float = typer.Option(
        0.0, "--against-offset",
        help="Offset constante ya aplicado al proyecto contra el que mediste (0 si mediste contra las fuentes sin corregir, p.ej. es_vocals.flac/en_vocals.flac por separado)",
    ),
    apply: bool = typer.Option(False, "--apply", help="Escribir el resultado en align.json. Sin esto, solo lo muestra."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Calcula offset+drift a partir de N>=2 puntos (ES, EN) medidos a
    oido — exacto con 2 puntos, por minimos cuadrados con mas. Ver
    remaster/steps/align.py:calibrate_from_points para la formula.
    """
    try:
        result, step_ran = run_calibration(episode, es_time, en_time, against_offset, apply, force)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title="Calibracion")
    table.add_column("Punto")
    table.add_column("ES (s)")
    table.add_column("EN (s)")
    table.add_column("Residuo (s)")
    for i, (es, en, r) in enumerate(zip(es_time, en_time, result.residuals), start=1):
        table.add_row(str(i), f"{es:.3f}", f"{en:.3f}", f"{r:+.4f}")
    console.print(table)
    console.print(f"offset_seconds = {result.offset_seconds:+.4f}")
    console.print(f"drift_slope    = {result.drift_slope:+.7f}")

    if apply:
        tag = "[yellow]aplicado[/yellow]" if step_ran else "[dim]sin cambios (ya estaba aplicado)[/dim]"
        console.print(f"align.json: {tag}")
    else:
        console.print(
            "\nPara aplicarlo directamente:\n"
            f"  remaster run {episode} --from project --to render "
            f"--sync-offset {result.offset_seconds:.4f} --sync-drift {result.drift_slope:.7f}"
        )


@app.command()
def status(episode: Path = typer.Argument(..., help="Carpeta del episodio")) -> None:
    """Muestra que pasos hay registrados en el manifiesto y cuando corrieron."""
    layout = EpisodeLayout(root=episode.expanduser().resolve())
    manifest = Manifest(layout.manifest_path)
    if not manifest.data:
        console.print(f"[dim]Sin manifiesto todavia en {layout.manifest_path}[/dim]")
        raise typer.Exit(0)

    table = Table(title=f"Estado — {layout.code}")
    table.add_column("Paso")
    table.add_column("Ultima ejecucion")
    table.add_column("Duracion")
    for step_name in sorted(manifest.data):
        record = manifest.data[step_name]
        table.add_row(step_name, record.get("recorded_at", "-"), f"{record.get('duration_seconds', 0)}s")
    console.print(table)


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Host de la UI local"),
    port: int = typer.Option(8765, help="Puerto de la UI local"),
    movies_dir: Optional[Path] = typer.Option(None, "--movies-dir", help="Carpeta base de partida (opcional: tambien se puede elegir desde la UI)"),
) -> None:
    """Arranca la UI web local (necesita el extra 'ui': uv sync --extra ui).

    La carpeta base se puede cambiar desde el navegador y se recuerda,
    asi que `--movies-dir` y `REMASTER_MOVIES_DIR` solo fijan por donde
    empieza la primera vez.
    """
    try:
        import uvicorn
    except ImportError as exc:
        console.print("[red]Falta el extra 'ui'.[/red] Instala con: uv sync --extra ui")
        raise typer.Exit(1) from exc

    if movies_dir:
        os.environ["REMASTER_MOVIES_DIR"] = str(movies_dir.expanduser().resolve())

    console.print(f"[green]UI en http://{host}:{port}[/green]  (Ctrl+C para parar)")
    uvicorn.run("remaster.web.app:app", host=host, port=port, reload=False)


@app.command()
def notes(episode: Path = typer.Argument(..., help="Carpeta del episodio")) -> None:
    """Genera <ep>/NOTES.md desde el manifiesto (ver remaster/notes.py)."""
    layout = EpisodeLayout(root=episode.expanduser().resolve())
    manifest = Manifest(layout.manifest_path)
    if not manifest.data:
        console.print(f"[dim]Sin manifiesto todavia en {layout.manifest_path} — ejecuta 'remaster run' primero[/dim]")
        raise typer.Exit(1)
    path = write_notes(layout, manifest)
    console.print(f"[green]Escrito:[/green] {path}")


@app.command()
def verify(
    episode: Path = typer.Argument(..., help="Carpeta del episodio"),
    stats: Optional[Path] = typer.Option(
        None, "--stats",
        help="Fichero de medida de REAPER. Sin esto se busca el mas reciente en el temporal del sistema.",
    ),
    no_stats: bool = typer.Option(False, "--no-stats", help="Analizar sin la medida de REAPER (analisis parcial)"),
    profile_path: Optional[Path] = typer.Option(None, "--profile", help="Perfil de serie (por defecto profiles/sh1984.toml)"),
    open_report: bool = typer.Option(False, "--open", help="Abrir el informe HTML al terminar"),
) -> None:
    """Re-analiza el proyecto tal como lo has dejado tras sincronizar a mano.

    Lee el .RPP editado (no `loudness.json`) y compara el nivel resultante
    de cada item contra EN VOX, para señalar tramos concretos que revisar y
    que hacer con cada uno.

    Para el analisis completo, antes en REAPER: selecciona las pistas a
    analizar y ejecuta la accion "Calculate loudness of selected tracks via
    dry run render".
    """
    layout = EpisodeLayout(root=episode.expanduser().resolve())
    profile = load_profile(profile_path)

    stats_path = None
    if not no_stats:
        stats_path = stats.expanduser().resolve() if stats else newest_render_stats(stats_search_dirs(layout))
        if stats_path is None:
            console.print(
                "[yellow]No encuentro ninguna medida de REAPER.[/yellow] Para el analisis completo, "
                "en REAPER: selecciona las pistas a analizar y ejecuta la accion "
                "'Calculate loudness of selected tracks via dry run render'. Sigo sin ella."
            )
        elif not stats_path.exists():
            raise typer.BadParameter(f"no existe {stats_path}")

    try:
        report = verify_project(layout, profile, stats_path)
    except VerifyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    actions = build_actions(report)
    console.print(f"\n[bold]Qué tengo que hacer — {layout.code}[/bold]")
    if not actions:
        console.print("[green]Nada. El castellano cuadra con el ingles en todo el episodio.[/green]")
    for n, action in enumerate(actions, start=1):
        colour = {"alta": "red", "media": "yellow"}.get(action.urgency, "cyan")
        # highlight=False: rich colorea cada numero por su cuenta y estas
        # lineas van llenas de timecodes y dB — queda ilegible.
        console.print(f"\n[{colour}][bold]{n}. {action.title}[/bold][/{colour}]", highlight=False)
        console.print(f"   {action.why}", highlight=False)
        console.print(f"   [bold]Qué hacer:[/bold] {action.how}", highlight=False)
        for spot in action.spots[:12]:
            console.print(f"     · {spot}", highlight=False)
        if len(action.spots) > 12:
            console.print(
                f"     · … y {len(action.spots) - 12} más, en el informe completo", highlight=False)

    console.print(
        f"\n[dim]Detalle — {report.item_count} items en «{report.es_track}», "
        f"fuentes: {', '.join(report.sources) or '—'}, "
        f"ganancia global {report.global_gain_db:+.2f} dB[/dim]"
    )

    if report.global_check:
        g = report.global_check
        table = Table(title="Global (post-FX)")
        table.add_column("")
        table.add_column(report.es_track, justify="right")
        table.add_column(report.en_track, justify="right")
        table.add_column("delta", justify="right")
        table.add_row("LUFS-I", f"{g.es_lufs_i:.1f}", f"{g.en_lufs_i:.1f}", f"{g.delta_db:+.2f} dB")
        table.add_row("LRA", f"{g.es_lra:.1f}", f"{g.en_lra:.1f}", f"{g.es_lra - g.en_lra:+.1f}")
        table.add_row("Pico dBFS", f"{g.es_peak_dbfs:.1f}", f"{g.en_peak_dbfs:.1f}", f"{g.headroom_db:.1f} dB de margen")
        table.add_row("Clips", str(g.es_clips), "—", "")
        console.print(table)
        if g.headroom_db < 1.0:
            console.print(f"[red]Margen de pico: solo {g.headroom_db:.1f} dB.[/red]")

    if report.envelope:
        e = report.envelope
        console.print(
            f"Envolvente: sd {e.sd_with:.2f} dB con / {e.sd_without:.2f} dB sin "
            f"({e.windows_out_with} vs {e.windows_out_without} ventanas fuera de {len(report.windows)}) — "
            f"[bold]{e.verdict}[/bold]"
        )

    if report.fx_tax:
        t = report.fx_tax
        verb = "suman" if t.tax_db >= 0 else "restan"
        console.print(f"Cadena de FX: {verb} {abs(t.tax_db):.2f} dB al pico (predicho {t.predicted_peak_dbfs:.2f} -> medido {t.measured_peak_dbfs:.2f} dBFS)")
        if t.predicted_peak_dbfs > 0:
            console.print("[red]El pico pre-FX predicho pasa de 0 dBFS: solo los FX evitan el recorte.[/red]")

    flagged = report.flagged_items
    if flagged:
        table = Table(title=f"Items a revisar ({len(flagged)} de {len(report.comparable_items)} comparables)")
        table.add_column("inicio")
        table.add_column("dur", justify="right")
        table.add_column("fuente")
        table.add_column("env", justify="right")
        table.add_column("delta", justify="right")
        table.add_column("motivo")
        for item in sorted(flagged, key=lambda i: -abs(i.delta_db))[:30]:
            colour = "red" if item.status == STATUS_DOUBLE_GAIN else "yellow"
            table.add_row(
                format_timecode(item.start), f"{item.end - item.start:.2f}s", item.source,
                f"{item.envelope_db:+.2f}", f"[{colour}]{item.delta_db:+.2f} dB[/{colour}]", item.status,
            )
        console.print(table)
    else:
        console.print("[green]Ningun item fuera de tolerancia.[/green]")

    for window in report.flagged_windows:
        console.print(
            f"  ventana {format_timecode(window.start)}–{format_timecode(window.end)}: "
            f"{window.delta_db:+.2f} dB"
        )

    for warning in report.warnings:
        console.print(f"[yellow]aviso:[/yellow] {warning}")

    json_path, html_path = write_report(layout, report)
    console.print(f"[green]Informe:[/green] {html_path}  ([dim]{json_path}[/dim])")
    if open_report:
        import subprocess
        subprocess.run(["open", str(html_path)], check=False)


def stats_search_dirs(layout: EpisodeLayout) -> list[Path]:
    """Donde buscar el render_stats.html. REAPER lo escribe en el temporal
    del sistema ($TMPDIR en macOS), no junto al proyecto.
    """
    import tempfile
    return [Path(tempfile.gettempdir()), Path("/tmp"), layout.project_dir, layout.finals]


# --------------------------------------------------------------------------
# Laboratorio de separacion (MSST)
# --------------------------------------------------------------------------
# Music-Source-Separation-Training se usa desde fuera: se lee su catalogo de
# configs y se ejecuta su `inference.py` con el interprete de su propio venv.
# Nunca se escribe dentro de su arbol, para que pueda seguir en `main` y
# actualizarse con `git pull`.


def msst_setting(name: str, override: Optional[str] = None) -> Optional[str]:
    """Un ajuste de MSST: primero el flag, luego el entorno, luego el .env.

    El .env se mira aqui y no solo en la UI para que los comandos funcionen
    igual desde una terminal, sin exportar nada a mano.
    """
    if override:
        return override
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    return read_env(ENV_PATH).get(name)


def build_msst_repo(
    repo_dir: Optional[str] = None, data_dir: Optional[str] = None
) -> MsstRepo:
    root = msst_setting("MSST_REPO_DIR", repo_dir)
    if not root:
        console.print(
            "[red]No se donde esta Music-Source-Separation-Training.[/red]\n"
            "Pasa --repo, exporta MSST_REPO_DIR o ponlo en el .env:\n"
            "  MSST_REPO_DIR=/ruta/a/Music-Source-Separation-Training"
        )
        raise typer.Exit(1)
    data = msst_setting("MSST_DATA_DIR", data_dir) or str(DEFAULT_MSST_DATA_DIR)
    return MsstRepo(
        root=Path(root).expanduser().resolve(),
        data_dir=Path(data).expanduser().resolve(),
    )


def msst_catalog() -> list[MsstModel]:
    """Catalogo base mas el fichero local del usuario, si lo hay."""
    extra = msst_setting("MSST_CATALOG")
    extra_path = Path(extra).expanduser() if extra else ENV_PATH.parent / "msst_models.local.toml"
    return load_catalog(extra=extra_path)


def _require_ready(repo: MsstRepo, model: MsstModel, variant) -> None:
    problems = repo.problems()
    if problems:
        console.print("[red]No puedo usar MSST:[/red]")
        for problem in problems:
            console.print(f"  · {problem}")
        raise typer.Exit(1)

    status = repo.status(model, variant)
    if not status["config_ok"]:
        console.print(f"[red]Falta el config:[/red] {status['config_path']}")
        raise typer.Exit(1)
    if not status["checkpoint_ok"]:
        lang = f" --lang {variant.id}" if variant else ""
        console.print(
            f"[red]Faltan los pesos:[/red] {status['checkpoint_path']}\n"
            f"Descargalos con: remaster msst-fetch {model.id}{lang}"
        )
        raise typer.Exit(1)


class _Cancelled(Exception):
    """Han pedido parar desde fuera (la UI, o un kill a mano)."""


def _raise_on_sigterm(signum, frame) -> None:
    raise _Cancelled()


def _human_size(path: Path) -> str:
    """Un fragmento de tres segundos no puede salir como "0 MB"."""
    size = path.stat().st_size
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _stream(argv: list[str], cwd: Optional[Path] = None) -> int:
    """Ejecuta y reenvia la salida segun llega.

    `inference.py` informa con tqdm, que pinta con "\r" y sin salto de linea:
    hay que reenviar los trozos tal cual en vez de iterar por lineas, o la
    barra no se ve avanzar hasta que termina la fase.
    """
    proc = subprocess.Popen(
        argv, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read1(4096)
        if not chunk:
            break
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()
    return proc.wait()


@app.command("msst")
def msst_cmd(
    audio: Path = typer.Argument(..., help="Fichero de audio a separar"),
    model_id: str = typer.Option(..., "--model", "-m", help="Id del modelo del catalogo"),
    lang: Optional[str] = typer.Option(None, "--lang", help="Variante por idioma, si el modelo tiene"),
    out: Optional[Path] = typer.Option(None, "--out", help="Carpeta de salida"),
    fmt: str = typer.Option("flac24", "--format", help="flac24 o wav32"),
    extract_instrumental: bool = typer.Option(False, "--extract-instrumental", help="Invertir para sacar el complementario"),
    tta: bool = typer.Option(False, "--tta", help="Test time augmentation: triplica el tiempo, limpia un poco"),
    cpu: bool = typer.Option(False, "--cpu", help="Forzar CPU en vez de la GPU/MPS"),
    repo_dir: Optional[str] = typer.Option(None, "--repo", help="Carpeta del clon de MSST"),
    events: Optional[Path] = typer.Option(None, "--events", help="Traza NDJSON para la UI"),
) -> None:
    """Separa un audio suelto con un modelo del catalogo de MSST."""
    log = EventLog(events)
    audio = audio.expanduser().resolve()
    if not audio.is_file():
        console.print(f"[red]No existe el fichero:[/red] {audio}")
        raise typer.Exit(1)

    repo = build_msst_repo(repo_dir)
    try:
        model = find_model(msst_catalog(), model_id)
        variant = model.variant(lang)
    except MsstError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if fmt not in FORMATS:
        console.print(f"[red]Formato desconocido:[/red] {fmt}. Usa: {', '.join(FORMATS)}")
        raise typer.Exit(1)
    _require_ready(repo, model, variant)

    out_dir = Path(msst_setting("MSST_OUTPUT_DIR", str(out) if out else None)
                   or audio.parent / "separaciones").expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = model.slug(variant)
    ext = "flac" if fmt == "flac24" else "wav"

    console.print(f"\n[bold]{model.name}[/bold]")
    console.print(f"  entrada  {audio.name}")
    console.print(f"  salida   {out_dir}")
    console.print(f"  pistas   {', '.join(model.stems) or 'las del config'}")
    if model.cpu_only and not cpu:
        console.print(
            "  [yellow]en CPU: este modelo no puede usar la GPU de Apple[/yellow]"
        )

    # `inference.py` solo mira dentro de una carpeta (glob recursivo), asi que
    # no acepta un fichero suelto. Se le da un directorio temporal con un
    # enlace al audio: sin copiar gigas y sin que se cuele ningun .DS_Store.
    work = Path(tempfile.mkdtemp(prefix="remaster-msst-"))
    # Cancelar desde la UI manda un SIGTERM, que por defecto mata el proceso
    # sin pasar por el `finally`: el temporal se quedaria ahi para siempre.
    # Convertido en excepcion, la limpieza corre igual que en una salida normal.
    previous = signal.signal(signal.SIGTERM, _raise_on_sigterm)
    try:
        log.start("preparar")
        link = work / "in" / audio.name
        link.parent.mkdir(parents=True)
        link.symlink_to(audio)
        raw_out = work / "out"
        raw_out.mkdir()
        log.close()

        log.start("separar")
        argv = build_command(
            repo, model, link.parent, raw_out,
            variant=variant, fmt=fmt,
            extract_instrumental=extract_instrumental,
            use_tta=tta, force_cpu=cpu,
        )
        code = _stream(argv, cwd=repo.root)
        if code != 0:
            log.fail(f"inference.py ha salido con codigo {code}")
            console.print(f"\n[red]La separacion ha fallado (codigo {code}).[/red]")
            raise typer.Exit(code)
        log.close()

        # Los nombres quedan planos y con el modelo dentro, para poder probar
        # dos modelos sobre el mismo audio sin que uno pise al otro.
        log.start("recoger")
        produced = sorted(p for p in raw_out.rglob("*") if p.is_file())
        if not produced:
            log.fail("inference.py no ha escrito ninguna pista")
            console.print("\n[red]No ha salido ninguna pista.[/red]")
            raise typer.Exit(1)

        console.print("")
        for path in produced:
            instr = path.stem.split("__")[-1]
            final = out_dir / f"{audio.stem}_{slug}_{instr}{path.suffix or '.' + ext}"
            shutil.move(str(path), final)
            console.print(f"  [green]{final.name}[/green]  {_human_size(final)}")
        log.close()
    except _Cancelled:
        console.print("\n[yellow]Cancelado.[/yellow]")
        raise typer.Exit(130)
    finally:
        signal.signal(signal.SIGTERM, previous)
        shutil.rmtree(work, ignore_errors=True)

    console.print("\n[green]Listo.[/green]")


def _download(url: str, dest: Path, label: str) -> None:
    """Descarga con reintentos. Zenodo devuelve 504 a ratos."""
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Optional[Exception] = None
    for attempt in range(4):
        try:
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                done = 0
                shown = -1
                with dest.open("wb") as handle:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        handle.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = done * 100 // total
                            if pct != shown:
                                shown = pct
                                print(f"\r{label}: {pct}% de {total / 1048576:.0f} MB",
                                      end="", flush=True)
                print("")
            return
        except Exception as exc:  # red, 504, corte a media descarga
            last = exc
            dest.unlink(missing_ok=True)
            wait = 5 * (attempt + 1)
            console.print(f"[yellow]{label}: fallo ({exc}). Reintento en {wait}s.[/yellow]")
            time.sleep(wait)
    raise MsstError(f"no he podido descargar {url}: {last}")


@app.command("msst-fetch")
def msst_fetch_cmd(
    model_id: str = typer.Argument(..., help="Id del modelo del catalogo"),
    lang: Optional[str] = typer.Option(None, "--lang", help="Variante por idioma"),
    repo_dir: Optional[str] = typer.Option(None, "--repo", help="Carpeta del clon de MSST"),
    force: bool = typer.Option(False, "--force", help="Volver a descargar aunque ya este"),
) -> None:
    """Descarga los pesos (y el config) de un modelo del catalogo."""
    repo = build_msst_repo(repo_dir)
    try:
        model = find_model(msst_catalog(), model_id)
        variant = model.variant(lang)
    except MsstError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    label = f"{model.id}{'/' + variant.id if variant else ''}"
    console.print(f"\n[bold]{label}[/bold]  →  {repo.data_dir}")

    config_url = model.config_url
    config_path = repo.resolve(model.config)
    if config_url and (force or not config_path.is_file()):
        _download(config_url, config_path, "config")

    ckpt_url = variant.checkpoint_url if variant else model.checkpoint_url
    needs_fix = variant.needs_fix if variant else model.needs_fix
    ckpt_path = repo.resolve(model.checkpoint_ref(variant))

    if not ckpt_url:
        console.print("[yellow]Este modelo no trae URL: los pesos vienen con el repo.[/yellow]")
        raise typer.Exit(0 if ckpt_path.is_file() else 1)
    if ckpt_path.is_file() and not force:
        console.print(f"[green]Ya estaba:[/green] {ckpt_path}")
        raise typer.Exit(0)

    try:
        if needs_fix:
            # Los pesos publicados de Bandit v2 son checkpoints de Lightning
            # enteros; MSST necesita el state dict pelado. Se baja a un fichero
            # aparte, se convierte y se borra el original de 425 MB.
            raw = ckpt_path.with_name(ckpt_path.name.replace("_fixed", ""))
            _download(ckpt_url, raw, "pesos")
            console.print("Convirtiendo el checkpoint de Lightning…")
            keys = fix_lightning_checkpoint(raw, ckpt_path)
            raw.unlink(missing_ok=True)
            console.print(f"  {keys} claves")
        else:
            _download(ckpt_url, ckpt_path, "pesos")
    except MsstError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    size = ckpt_path.stat().st_size / (1024 * 1024)
    console.print(f"\n[green]Listo:[/green] {ckpt_path}  ({size:.0f} MB)")


if __name__ == "__main__":
    app()
