# Audio-remaster

Pipeline determinista para reconstruir el audio de *Sherlock Holmes (1984)*: separa el dialogo en castellano de un DVD y lo remonta sobre la musica y efectos del Blu-ray, sincronizado al frame rate del Blu-ray, con render en REAPER y mux final a MKV.

Dado un episodio (una carpeta con las fuentes de video dentro), `remaster` extrae los audios, corrige el frame rate, separa los stems (dialogo vs musica+efectos), sincroniza y ajusta el loudness automaticamente, genera el proyecto REAPER, renderiza y mezcla el MKV final — todo reanudable y reproducible.

Ver [`SH1984/v2.0.0.md`](SH1984/v2.0.0.md) para el diseño del proceso, y [`SH1984/v1.0.0.md`](SH1984/v1.0.0.md) / [`v1.0.1.md`](SH1984/v1.0.1.md) / [`v1.1.0.md`](SH1984/v1.1.0.md) para el registro historico de como se hacia a mano.

## Requisitos

- macOS con Apple Silicon (probado en arm64).
- [REAPER](https://www.reaper.fm/download.php) instalado en `/Applications`.
- `ffmpeg`/`ffprobe` y `mkvtoolnix` (`mkvmerge`, `mkvextract`): `brew install ffmpeg mkvtoolnix`.
- Python 3.12 y [`uv`](https://docs.astral.sh/uv/).

`remaster doctor` comprueba todo esto (y con `--fix` propone los comandos de instalacion, pidiendo confirmacion antes de ejecutar nada).

## Instalacion

```bash
uv sync --extra audio                              # nucleo del pipeline
uv sync --extra audio --extra local-separation      # + separacion local (offline)
uv sync --extra audio --extra ui                    # + UI web local
```

## Carpeta de episodio

El pipeline usa la misma convencion de carpetas que ya se usaba a mano:

```
~/Movies/s03e01/
  00_sources/    # aqui van los ficheros de video de origen (DVD + Blu-ray + 4K opcional)
  01_stems/      # generado: separacion de stems
  02_finals/     # generado: render de REAPER
  s03e01/        # generado: proyecto REAPER (s03e01.RPP + s03e01_render.RPP)
  .remaster/     # generado: manifest.json, align.json, align.png
```

Solo hace falta poner en `00_sources/` los ficheros de video de origen (el rip del DVD y el Blu-ray; el remaster 4K es opcional). El pipeline detecta el rol de cada fichero por su contenido (idiomas de audio, codecs, resolucion) — no hace falta indicar indices de pista salvo en casos raros (`--es-track`, `--en-track`, etc.).

## Uso

```
REMASTER_MOVIES_DIR=~/Movies uv run remaster ui
```

```bash
remaster doctor                          # comprobar herramientas
remaster sources ~/Movies/s03e01         # ver que detecta, sin tocar nada
remaster run ~/Movies/s03e01             # pipeline completo, de punta a punta
remaster status ~/Movies/s03e01          # que pasos hay hechos y cuando
remaster notes ~/Movies/s03e01           # generar <ep>/NOTES.md desde lo que se ejecuto
remaster ui                              # UI web local (necesita --extra ui)
```

Por tramos, con reanudacion real (los pasos anteriores al `--from` no se recalculan, se localizan por convencion de nombre):

```bash
remaster run ~/Movies/s03e01 --to project        # hasta generar el .RPP, sin renderizar
# ... editas el .RPP a mano si hace falta (parchear huecos, ReaFir) ...
remaster run ~/Movies/s03e01 --from render        # renderiza y mezcla desde ahi
```

`--force` re-ejecuta un tramo aunque el manifiesto diga que esta al dia (por ejemplo, tras cambiar un parametro que el pipeline no detecta automaticamente).

## Backend de separacion

Por defecto, `--separator local` usa un modelo BS-Roformer en la propia maquina (offline, sin token). `--separator mvsep` usa el servicio hospedado como alternativa/comparacion — necesita `MVSEP_API_TOKEN` en el entorno (nunca por linea de comandos, para no dejarlo en el historial de shell).

## Desarrollo

```bash
uv sync --extra audio --extra local-separation --extra ui --extra dev
uv run pytest
```

## Licencia

GPL-3.0. Ver [`LICENSE`](LICENSE).
