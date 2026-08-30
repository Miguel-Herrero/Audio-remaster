"""Que hace cada paso y que protege, en una linea cada cosa.

Existe para que la UI no te haga ejecutar a ciegas: un desplegable con
`retime`, `separate`, `remux4k` no dice nada a quien no escribio el
pipeline, y desde que `project` tiene un guardian de ediciones manuales
conviene saber que salvaguarda tiene cada paso ANTES de pulsar, no al
fallar.

Fuente unica: la prosa vive aqui y solo aqui. La UI la consume por
`GET /api/steps` en vez de duplicarla en el HTML, que es como se
desincronizan las cosas. Los textos resumen los docstrings de
`remaster/steps/*.py`, que siguen siendo la explicacion larga.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Todos los pasos comparten el contrato de `Manifest.run_step`: si las
# entradas, los parametros y las salidas siguen igual, no se rehace nada.
# Se repite en cada `safeguard` porque la UI las muestra de una en una,
# no como lista.
_NOOP = "Si nada ha cambiado desde la ultima vez, no hace nada (no-op)."


@dataclass(frozen=True)
class StepInfo:
    key: str
    title: str
    summary: str
    safeguard: str
    # `manual`: no basta con pulsar el boton — hay algo que tienes que
    # hacer tu fuera de la UI para que el paso tenga sentido.
    manual: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


STEP_INFO: tuple[StepInfo, ...] = (
    StepInfo(
        key="ingest",
        title="Reconocer las fuentes",
        summary="Mira que hay en 00_sources y decide cual es el DVD castellano, cual el Blu-ray y cual el remaster 4K.",
        safeguard="Solo lee. Si no puede decidir, para y te lo dice en vez de adivinar.",
    ),
    StepInfo(
        key="extract",
        title="Sacar el audio",
        summary="Extrae a FLAC la pista castellana del DVD y la inglesa (y la japonesa) del Blu-ray.",
        safeguard=f"Conserva la profundidad de bits de cada origen; no recodifica de mas. {_NOOP}",
    ),
    StepInfo(
        key="retime",
        title="Ajustar la velocidad",
        summary="Lleva el audio del DVD al frame rate del Blu-ray (25 → 23.976 fps en esta serie).",
        safeguard=f"El ratio sale de los fps medidos, no de una constante. Si ya coinciden, se omite. {_NOOP}",
    ),
    StepInfo(
        key="separate",
        title="Separar voz y musica",
        summary="Parte cada idioma en dos pistas: voces por un lado, musica y efectos por otro.",
        safeguard=f"Es el paso lento, de varios minutos. {_NOOP}",
    ),
    StepInfo(
        key="align",
        title="Sincronizar ES con EN",
        summary="Calcula cuanto hay que desplazar el castellano para que cuadre con el ingles, y si ademas se va desviando.",
        safeguard=f"Corrige la desviacion con cortes en silencios, sin estirar el audio. {_NOOP}",
    ),
    StepInfo(
        key="loudness",
        title="Igualar el volumen",
        summary="Mide el volumen de los dos idiomas y calcula la ganancia global mas los retoques escena a escena.",
        safeguard=(
            "No sube tanto como para saturar: limita la ganancia segun el pico real de la señal. "
            "Solo escribe numeros en loudness.json — quien los aplica al proyecto es «Crear el proyecto»."
        ),
    ),
    StepInfo(
        key="project",
        title="Crear el proyecto de REAPER",
        summary="Escribe el .RPP con las pistas colocadas, el sync aplicado y el volumen ya ajustado.",
        safeguard=(
            "No pisa un .RPP que hayas editado a mano: lo compara con el que dejo la ultima vez y, "
            "si no coincide, aborta explicando como seguir."
        ),
    ),
    StepInfo(
        key="verify",
        title="Revisar lo que has editado",
        summary="Compara el proyecto tal como lo has dejado contra el ingles y te dice que tramos revisar y que hacer con cada uno.",
        safeguard=(
            "Solo lee: no toca el proyecto. Necesita que midas antes en REAPER; sin esa medida se omite "
            "en vez de darte un analisis a medias."
        ),
        manual=True,
    ),
    StepInfo(
        key="render",
        title="Renderizar la mezcla",
        summary="Renderiza el audio castellano final a FLAC, sin abrir REAPER.",
        safeguard=(
            "Deriva el proyecto de render de tu .RPP editado justo antes de renderizar, asi que recoge "
            f"tus cambios y las pistas que hayas añadido. {_NOOP}"
        ),
    ),
    StepInfo(
        key="mux",
        title="Montar el MKV",
        summary="Junta el video y los subtitulos del Blu-ray con el audio reconstruido, el ingles y el japones.",
        safeguard=f"Orden de pistas fijo (video, ES, EN, JA, subs), este o no el japones. {_NOOP}",
    ),
    StepInfo(
        key="remux4k",
        title="Cambiar al video 4K",
        summary="Rehace el MKV con el video del remaster 4K, si lo tienes en 00_sources.",
        safeguard=f"Sin fuente 4K no es un error: simplemente no se ejecuta. {_NOOP}",
    ),
)

BY_KEY: dict[str, StepInfo] = {info.key: info for info in STEP_INFO}


def step_keys() -> list[str]:
    """El orden de ejecucion, derivado de STEP_INFO.

    `remaster.cli.STEP_ORDER` toma de aqui su valor, para que el orden y la
    prosa no puedan separarse.
    """
    return [info.key for info in STEP_INFO]
