"""Paso 7: construir el proyecto REAPER (.RPP) desde las fuentes
extraidas/separadas/sincronizadas y el loudness ya calculado. No hay
sesion REAPER de por medio — se escribe el fichero directamente
(ver remaster/reaper/rpp.py).

Genera un unico fichero, `<ep>/<ep>.RPP`: todo audible, para retocar y
parchear a mano. El `<ep>_render.RPP` que se renderiza ya no se escribe
aqui — lo deriva el paso `render` a partir de este, para que recoja las
ediciones manuales (ver reaper/rpp_render.py).

Este paso NO pisa un `<ep>.RPP` que hayas editado. El manifiesto guarda
el hash del fichero tal y como lo escribio la ultima vez; si el de disco
no coincide, es que lo has tocado y regenerarlo se llevaria por delante
horas de sync a mano. Se aborta con un error que dice que hacer. Con
`--force` se procede, pero dejando antes una copia con marca de tiempo.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import replace
from pathlib import Path

from ..layout import EpisodeLayout
from ..manifest import Manifest, StepResult, sha256_file
from ..probe import probe_file
from ..profile import Profile
from ..reaper.rpp import (
    Item,
    Project,
    RenderSettings,
    Track,
    js_volume_fx_lines,
    reaeq_holmes_fx_lines,
    reafir_default_fx_lines,
    write_project,
)
from ..sources import ClassifiedSources
from .align import MAX_SANE_DRIFT_SLOPE, AlignResult, source_time_at
from .loudness import LoudnessResult

# Pistas que no deben sonar en el render final: el ingles hablado y el
# fondo musical castellano (se usa el ingles, que es el bueno). Por clave
# de pista, no por nombre — el nombre lo pone el perfil de serie.
MUTED_IN_RENDER = {"EN_VOX", "ES_ME"}
# Y las que si o si tienen que sonar, aunque el usuario las haya muteado
# para escuchar algo suelto y se le haya olvidado desmutearlas.
AUDIBLE_IN_RENDER = {"ES_VOX", "EN_ME"}


class ProjectEditedError(RuntimeError):
    """El `<ep>.RPP` de disco no es el que escribio el pipeline."""

# Umbral por debajo del cual el drift acumulado en todo el episodio no
# compensa trocear: un solo item con offset constante ya es indistinguible.
MIN_TOTAL_DRIFT_TO_SEGMENT_S = 0.05
# Marca "nominal" cada cuanto se busca un punto de corte. El corte real
# se desplaza al hueco de silencio en ES VOX mas cercano a esta marca
# (ver _choose_cut_points) para no partir un dialogo — nunca cae
# exactamente aqui salvo que no se encuentre silencio cerca.
SEGMENT_LENGTH_S = 300.0
# Cuanto se permite buscar a los dos lados de cada marca nominal antes de
# rendirse y cortar ahi de todos modos.
CUT_SEARCH_WINDOW_S = 45.0
# Silencio minimo sostenido para considerar un hueco valido como corte —
# mas laxo que una frontera de escena completa (basta una pausa breve).
CUT_MIN_SILENCE_S = 0.2
# Fundido en cada corte interno, solo para evitar un click en el empalme
# — no es time-stretch, el audio dentro de cada tramo se reproduce tal
# cual (PLAYRATE 1.0 siempre). Mismo tipo de tecnica que ya usa el
# proyecto real de referencia para parchear huecos a mano (EN M&E
# troceado en ~40 items en s02e06.RPP).
SEGMENT_CROSSFADE_S = 0.03


def _duration(path: Path) -> float:
    return probe_file(path).duration


def _position_and_soffs(align: AlignResult, t_start: float) -> tuple[float, float]:
    """El punto del fichero fuente ES que debe sonar en el instante
    `t_start` de la linea de tiempo es `source_time_at(align, t_start)`
    (ver steps/align.py — misma formula que usa loudness.py para no medir
    un tramo de audio distinto del que realmente suena ahi). Si ese punto
    cae antes del principio del fichero (offset muy negativo), no se
    puede usar SOFFS negativo: se retrasa el item en vez de recortarlo.
    """
    src_t = source_time_at(align, t_start)
    if src_t >= 0:
        return t_start, src_t
    return t_start - src_t, 0.0


def choose_cut_points(
    es_vox_path: Path,
    reference_duration: float,
    segment_length: float = SEGMENT_LENGTH_S,
    search_window: float = CUT_SEARCH_WINDOW_S,
    silence_threshold_db: float = -35.0,
    min_silence_s: float = CUT_MIN_SILENCE_S,
) -> list[float]:
    """Puntos de corte para trocear ES VOX/ES M&E: uno cada `segment_length`
    aprox., desplazado al hueco de silencio de ES VOX mas cercano dentro
    de +-search_window (para no cortar en mitad de una linea de dialogo).
    Si no hay silencio cerca de una marca, se usa la marca nominal tal
    cual — mejor un corte en audio activo que perder la correccion.
    """
    from ..audio.envelope import energy_envelope_db, load_mono
    from ..audio.scenes import find_quiet_point_near

    samples, sample_rate = load_mono(es_vox_path)
    envelope_db, frame_rate = energy_envelope_db(samples, sample_rate)

    points = []
    nominal = segment_length
    while nominal < reference_duration:
        quiet = find_quiet_point_near(
            envelope_db, frame_rate, nominal, search_window, silence_threshold_db, min_silence_s,
        )
        points.append(quiet if quiet is not None else nominal)
        nominal += segment_length
    return points


def _drift_corrected_items(
    source_path: Path,
    reference_duration: float,
    source_duration: float,
    align: AlignResult,
    cut_points: tuple[float, ...] = (),
) -> tuple[Item, ...]:
    """Items para una pista ES, corrigiendo offset constante y deriva
    lineal SIN estirar ni comprimir el audio: solo cortes (en los puntos
    de `cut_points`, tipicamente silencios de ES VOX) + un fundido corto
    en cada empalme para evitar clicks. Si la deriva total en todo el
    episodio es despreciable, se devuelve un unico item sin cortes.
    """
    clamped_drift = max(-MAX_SANE_DRIFT_SLOPE, min(MAX_SANE_DRIFT_SLOPE, align.drift_slope))
    align = replace(align, drift_slope=clamped_drift)
    total_drift = abs(clamped_drift) * reference_duration

    if total_drift < MIN_TOTAL_DRIFT_TO_SEGMENT_S or not cut_points:
        position, soffs = _position_and_soffs(align, 0.0)
        length = min(reference_duration, max(0.0, source_duration - soffs))
        return (Item(position=position, length=length, soffs=soffs, name=source_path.name, source_file=source_path),)

    # `position` hace doble papel: es donde se coloca el item en la linea
    # de tiempo Y el instante de referencia (tiempo EN, que nunca se
    # desplaza) en el que evaluar el offset local. Los tramos deben
    # quedar contiguos entre si (sin huecos ni solapes): solo el primero
    # puede llevar un desplazamiento inicial si el offset de partida es
    # negativo. `reference_covered` es independiente y mide cuanto del
    # episodio (en tiempo EN) ya se ha cubierto, para saber cuando parar.
    edges = [*sorted(cut_points), reference_duration]
    items = []
    position = 0.0
    reference_covered = 0.0
    first = True
    for edge in edges:
        if reference_covered >= reference_duration:
            break
        item_position, soffs = _position_and_soffs(align, position)
        if item_position != position:
            # Solo puede pasar en el primer tramo (offset inicial negativo):
            # hay que saltar `position` al punto real donde arranca el audio.
            position = item_position

        seg_len = max(0.0, edge - reference_covered)
        available = max(0.0, source_duration - soffs)
        length = min(seg_len, available)
        if length <= 0:
            break
        items.append(Item(
            position=item_position, length=length, soffs=soffs, name=source_path.name, source_file=source_path,
            fade_in=SEGMENT_CROSSFADE_S if not first else 0.01,
            fade_out=SEGMENT_CROSSFADE_S,
        ))
        first = False
        reference_covered += seg_len
        position = item_position + length

    return tuple(items)


def build_tracks(
    sources: ClassifiedSources,
    profile: Profile,
    en_vox_path: Path,
    en_me_path: Path,
    es_vox_path: Path,
    es_me_path: Path,
    align: AlignResult,
    loudness: LoudnessResult,
    episode_code: str,
) -> tuple[Track, ...]:
    en_vox_len = _duration(en_vox_path)
    en_me_len = _duration(en_me_path)

    clamped_drift = max(-MAX_SANE_DRIFT_SLOPE, min(MAX_SANE_DRIFT_SLOPE, align.drift_slope))
    needs_segments = abs(clamped_drift) * en_vox_len >= MIN_TOTAL_DRIFT_TO_SEGMENT_S
    # Mismos puntos de corte para ES VOX y ES M&E: van sincronizados entre
    # si, asi que deben partirse exactamente en los mismos instantes.
    cut_points = tuple(choose_cut_points(es_vox_path, en_vox_len)) if needs_segments else ()

    es_vox_items = _drift_corrected_items(es_vox_path, en_vox_len, _duration(es_vox_path), align, cut_points)
    es_me_items = _drift_corrected_items(es_me_path, en_vox_len, _duration(es_me_path), align, cut_points)

    video_track = Track(
        key="VIDEO", name=profile.tracks.video, mainsend=False,
        items=(Item(
            position=0.0, length=en_vox_len, name=sources.bluray_container.name,
            source_file=sources.bluray_container, source_kind="VIDEO",
        ),),
    )
    en_vox_track = Track(
        key="EN_VOX", name=profile.tracks.en_vox, mainsend=True,
        items=(Item(position=0.0, length=en_vox_len, name=en_vox_path.name, source_file=en_vox_path),),
    )
    en_me_track = Track(
        key="EN_ME", name=profile.tracks.en_me, mainsend=True,
        items=(Item(position=0.0, length=en_me_len, name=en_me_path.name, source_file=en_me_path),),
    )
    es_me_track = Track(
        key="ES_ME", name=profile.tracks.es_me, mainsend=True,
        items=es_me_items,
    )

    fx_lines = (
        js_volume_fx_lines(loudness.global_gain_db, episode_code, "ES_VOX")
        + reaeq_holmes_fx_lines(episode_code, "ES_VOX")
        + reafir_default_fx_lines(episode_code, "ES_VOX")
    )
    es_vox_track = Track(
        key="ES_VOX", name=profile.tracks.es_vox, mainsend=True,
        items=es_vox_items,
        vol_points=loudness.scene_vol_points,
        fx_lines=fx_lines,
    )

    return (video_track, en_vox_track, en_me_track, es_vox_track, es_me_track)



def project_hand_edited(layout: EpisodeLayout, manifest: Manifest) -> bool:
    """True si el `<ep>.RPP` de disco no es el que dejo este paso.

    El manifiesto ya guarda el hash de cada salida, asi que la
    comprobacion es exacta: distinto hash = lo has editado.

    Es un predicado suelto, y no logica escondida dentro del guardian,
    porque la UI lo necesita para poder AVISARTE antes de que pulses
    «Ejecutar» — enterarse de que el paso iba a machacar tu trabajo por
    el error que salta al intentarlo llega tarde.
    """
    if not layout.rpp_edit.exists():
        return False
    recorded = (manifest.get("project") or {}).get("outputs", {}).get(str(layout.rpp_edit))
    if recorded is None:
        return False
    return sha256_file(layout.rpp_edit) != recorded


def _guard_hand_edits(layout: EpisodeLayout, manifest: Manifest, force: bool) -> None:
    """Aborta si el proyecto esta editado a mano.

    Sin esto, un cambio en cualquier parametro del paso (por ejemplo la
    ganancia global, que cambia sola al recalcular loudness) basta para
    que el paso se considere desactualizado y reescriba el proyecto
    encima de todo el trabajo manual.
    """
    if not project_hand_edited(layout, manifest):
        return
    if not force:
        raise ProjectEditedError(
            f"{layout.rpp_edit} tiene cambios que no hizo el pipeline (lo has editado a mano).\n"
            "Regenerarlo borraria esos cambios. Opciones:\n"
            "  - salta este paso: ejecuta el rango desde 'render' en adelante;\n"
            "  - si de verdad quieres regenerarlo, repite con --force "
            "(se guarda una copia con marca de tiempo antes de escribir)."
        )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = layout.rpp_edit.with_suffix(f".RPP.bak-{stamp}")
    shutil.copy2(layout.rpp_edit, backup)

def build_project_step(
    sources: ClassifiedSources,
    layout: EpisodeLayout,
    profile: Profile,
    en_vox_path: Path,
    en_me_path: Path,
    es_vox_path: Path,
    es_me_path: Path,
    align: AlignResult,
    loudness: LoudnessResult,
    manifest: Manifest,
    force: bool = False,
) -> StepResult:
    params = {
        "offset_seconds": align.offset_seconds,
        "drift_slope": align.drift_slope,
        "global_gain_db": loudness.global_gain_db,
        # El contenido de los puntos, no solo cuantos hay: dos loudness.json
        # distintos pueden tener el mismo numero de escenas con ganancias
        # diferentes (p.ej. tras arreglar como se mide cada escena), y eso
        # tiene que invalidar el proyecto igual que si cambiara el numero.
        "scene_vol_points": [[p.time, p.gain, p.shape] for p in loudness.scene_vol_points],
        "reaeq_preset": profile.reaeq.preset_name,
        "track_names": profile.tracks.__dict__,
        # Constantes del troceado por drift: si se ajustan alguna vez, un
        # proyecto ya cacheado tiene que regenerarse, no quedarse con
        # cortes calculados con las constantes viejas.
        "segment_length_s": SEGMENT_LENGTH_S,
        "cut_search_window_s": CUT_SEARCH_WINDOW_S,
        "cut_min_silence_s": CUT_MIN_SILENCE_S,
        "segment_crossfade_s": SEGMENT_CROSSFADE_S,
        "min_total_drift_to_segment_s": MIN_TOTAL_DRIFT_TO_SEGMENT_S,
        "max_sane_drift_slope": MAX_SANE_DRIFT_SLOPE,
    }

    def fn() -> None:
        _guard_hand_edits(layout, manifest, force)
        tracks = build_tracks(
            sources, profile, en_vox_path, en_me_path, es_vox_path, es_me_path,
            align, loudness, layout.code,
        )
        edit_project = Project(
            episode_code=layout.code, tracks=tracks,
            render=RenderSettings(output_file=layout.es_reconstructed),
        )
        write_project(edit_project, layout.rpp_edit)

    return manifest.run_step(
        step_name="project",
        input_paths=[en_vox_path, en_me_path, es_vox_path, es_me_path, layout.align_json_path],
        params=params,
        output_paths=[layout.rpp_edit],
        tool_versions={},
        fn=fn,
        force=force,
    )
