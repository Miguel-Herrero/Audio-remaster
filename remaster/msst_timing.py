"""Cuanto va a tardar una separacion.

Separar un episodio entero en CPU puede irse a la media hora, y sin una cifra
delante no hay forma de decidir si esperar o cambiar de modelo. El coste tiene
dos partes bien distintas — cargar el modelo, que es constante, y procesar, que
crece con la duracion — asi que se estima con una recta:

    tiempo = carga + ritmo * segundos_de_audio

Los valores de fabrica salen de medir los modelos en un Apple Silicon y sirven
para la primera vez. A partir de ahi **manda lo que tarda en tu maquina**: cada
ejecucion se apunta y, en cuanto hay dos duraciones distintas, la recta se
ajusta por minimos cuadrados con tus propias medidas. Una GPU distinta, un disco
mas lento o un modelo que no esta en el catalogo se corrigen solos.

Una honestidad sobre el modelo: al medirlo, los tiempos crecen **peor que
lineal** con la duracion — un audio tres veces mas largo puede tardar mucho mas
del triple, seguramente por presion de memoria. Una recta se queda corta ahi, y
la cifra que sale es un suelo, no una promesa. Se acepta a proposito: ajustarla
con los tamanos que de verdad uses sale mejor que inventar una curva con dos
puntos, y para eso esta el historial. Si sueles separar fragmentos parecidos, la
estimacion converge a lo que te pasa a ti.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Cuantas medidas se guardan por modelo y dispositivo. Suficientes para
# promediar, pocas como para que un cambio de maquina se note enseguida.
MAX_SAMPLES = 20

# Con una sola medida no se pueden separar carga y ritmo (una recta necesita
# dos puntos), asi que se conserva el ritmo de fabrica y solo se ajusta la
# constante. Por debajo de esto, la medida es demasiado corta para fiarse.
MIN_SECONDS_TO_LEARN = 5.0


@dataclass(frozen=True)
class Speed:
    """Coste de un modelo: una parte fija y otra por segundo de audio."""

    load_s: float
    rate: float  # segundos de proceso por segundo de audio

    def estimate(self, audio_seconds: float) -> float:
        return self.load_s + self.rate * max(audio_seconds, 0.0)


# Para un modelo que no diga lo suyo. Medido en un Apple Silicon con
# BS-Roformer: separar cuesta bastante mas que reproducir, incluso en GPU —
# 4,4 segundos de proceso por cada segundo de audio. Conviene que estos numeros
# pequen de pesimistas: sorprende mejor terminar antes que despues.
FALLBACK = {
    "mps": Speed(load_s=4.4, rate=4.4),
    "cpu": Speed(load_s=5.0, rate=9.0),
}


def device_for(model, force_cpu: bool = False) -> str:
    """El dispositivo que va a acabar usando `inference.py`.

    No se le pregunta a torch: los modelos marcados `cpu_only` acaban en CPU
    aunque haya GPU, y esa marca es justo lo que hace la estimacion realista.
    """
    if force_cpu or getattr(model, "cpu_only", False):
        return "cpu"
    return "mps" if _has_mps() else "cpu"


def _has_mps() -> bool:
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.backends.mps.is_available())
    except AttributeError:
        return False


def key_for(model_id: str, variant_id: Optional[str], device: str) -> str:
    return f"{model_id}{'/' + variant_id if variant_id else ''}@{device}"


# --------------------------------------------------------------------------
# Historial
# --------------------------------------------------------------------------

def load_samples(path: Path) -> dict[str, list[list[float]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, PermissionError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record(path: Path, key: str, audio_seconds: float, elapsed: float) -> None:
    """Apunta lo que ha tardado una ejecucion real."""
    if audio_seconds < MIN_SECONDS_TO_LEARN or elapsed <= 0:
        return
    samples = load_samples(path)
    entries = [e for e in samples.get(key, []) if isinstance(e, list) and len(e) == 2]
    entries.append([round(audio_seconds, 2), round(elapsed, 2)])
    samples[key] = entries[-MAX_SAMPLES:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    except OSError:
        # Estimar mejor es una comodidad: si el disco dice que no, se sigue
        # separando igual con los valores de fabrica.
        pass


def fit(entries: list[list[float]], fallback: Speed) -> Optional[Speed]:
    """Ajusta la recta a las medidas. `None` si no dan para nada."""
    points = [(x, y) for x, y in entries if x >= MIN_SECONDS_TO_LEARN and y > 0]
    if not points:
        return None

    duraciones = {round(x, 1) for x, _ in points}
    if len(duraciones) < 2:
        # Un solo tamano de audio: se respeta el ritmo conocido y se despeja
        # la carga, en vez de inventar una pendiente con un unico punto.
        x, y = points[-1]
        return Speed(load_s=max(y - fallback.rate * x, 0.0), rate=fallback.rate)

    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None

    rate = (n * sxy - sx * sy) / denom
    load = (sy - rate * sx) / n
    if rate <= 0:
        # Ruido de medida: mas audio nunca tarda menos.
        return None
    return Speed(load_s=max(load, 0.0), rate=rate)


def speed_for(
    model, variant_id: Optional[str], device: str, samples_path: Optional[Path] = None
) -> tuple[Speed, str]:
    """La mejor estimacion disponible y de donde sale.

    El origen se devuelve para poder decirlo en la interfaz: no es lo mismo una
    cifra de fabrica que una sacada de tus ultimas ejecuciones.
    """
    fallback = _catalog_speed(model, device) or FALLBACK.get(device, FALLBACK["cpu"])
    if samples_path is not None:
        entries = load_samples(samples_path).get(key_for(model.id, variant_id, device), [])
        learned = fit(entries, fallback)
        if learned is not None:
            return learned, f"{len(entries)} ejecuciones tuyas"
    return fallback, "estimacion de fabrica"


def _catalog_speed(model, device: str) -> Optional[Speed]:
    for name, load, rate in getattr(model, "speed", ()) or ():
        if name == device and rate > 0:
            return Speed(load_s=load, rate=rate)
    return None


def format_eta(seconds: float) -> str:
    """Redondeado a lo que se puede sostener: nadie acierta el segundo."""
    if seconds < 45:
        return "menos de un minuto"
    minutes = seconds / 60
    if minutes < 10:
        return f"~{round(minutes)} min"
    if minutes < 60:
        return f"~{round(minutes / 5) * 5} min"
    hours = minutes / 60
    return f"~{hours:.1f} h".replace(".0 h", " h")
