# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado con [SemVer](https://semver.org/lang/es/).

## [2.0.0] - 2026-08-29

### Added
- Pipeline determinista completo (`remaster` package): ingest, extract, retime,
  separate, align, loudness, project, render, mux, remux4k.
- CLI (`remaster doctor|run|step|status|clean|calibrate|notes|ui`) con
  reanudación por pasos vía manifiesto (`.remaster/manifest.json`: hashes de
  entrada, parámetros, versiones de herramientas).
- Clasificación automática de fuentes por contenido (`remaster/sources.py`):
  DVD castellano, Blu-ray de referencia, remaster 4K opcional (solo vídeo).
- Sincronización automática ES↔EN por GCC-PHAT con detección de deriva
  (`remaster/audio/xcorr.py`), corregida por cortes en silencio, no por
  time-stretch.
- Calibración manual de sync (2 puntos exactos o N puntos por mínimos
  cuadrados) vía `remaster calibrate` y `/api/calibrate`.
- Igualación de sonoridad LUFS global y por escena, con límite de ganancia
  segura por pico (`remaster/audio/lufs.py`).
- Backend de separación de stems intercambiable: local (`audio-separator`,
  offline) o MVSEP (remoto).
- Generador de proyectos REAPER (`.RPP`) determinista desde dataclasses, sin
  sesión interactiva de REAPER ni `reapy`: preset ReaEQ "Holmes_1.0.1"
  embebido, envolvente de volumen por escena, FX ReaFir (Cockos) incluido
  desactivado y listo para captura manual de perfil de ruido.
- Render headless vía `REAPER -renderproject`.
- Mux final a MKV con orden de pistas fijo (vídeo, ES, EN, JA, subs) y
  nombrado de fichero final configurable (`--episode-title`/`--series-title`)
  siguiendo la convención `<serie> <SxxExx> <título> - [<altura>p <códec>]
  [FLAC <idiomas>] [Subs <idiomas>]`.
- Remux opcional del vídeo por el remaster 4K, sin re-codificar.
- UI web local (FastAPI) como capa fina sobre la CLI: estado de episodios,
  lanzar ejecuciones, calibrado manual colapsable, previsualización de
  `align.png`.
- Suite de tests unitarios (pytest) cubriendo fuentes, retiming, xcorr,
  loudness, generación de `.RPP` (snapshot determinista), mux y nombrado.

### Changed
- Sustituye el flujo manual/`reapy_boost` de la rama `automation-steps` por
  un pipeline reproducible de carpeta a MKV.

### Fixed
- Corrige la premisa invertida y los ejemplos de CLI obsoletos del README.
- Corrige la documentación de origen del audio inglés en
  `SH1984/v1.0.1.md` (venía del Blu-ray, no del remaster 4K).
