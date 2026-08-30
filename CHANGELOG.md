# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado con [SemVer](https://semver.org/lang/es/).

## [2.1.0] - 2026-08-30

### Added
- `remaster verify <episodio>`: re-analiza el proyecto TAL COMO queda tras
  editarlo a mano en REAPER. Lee el `.RPP` (no `loudness.json`) y compara el
  nivel resultante de cada item contra EN VOX, en vez de comprobar de qué
  fichero sale cada uno — rellenar huecos con otra fuente es parte del
  flujo, no un fallo. Detecta el caso en que un trozo del inglés puesto en
  su sitio recibe encima la envolvente de compensación del castellano
  ("ganancia doble"). Con el `render_stats.html` de un dry run añade la
  medida post-FX: LUFS-I global, pico real, deriva por ventanas de 2 min,
  puntos sueltos más desviados, y un A/B que dice si la envolvente sigue
  ayudando tras re-sincronizar.
- Informe en `.remaster/verify.json` y `.remaster/verify.html`.
- Botón «Re-analizar tras sincronizar a mano» en la UI web, con el estado
  del `render_stats.html` detectado y las instrucciones del dry run.
- Lector de `.RPP` (`remaster/reaper/rpp_read.py`) y de `render_stats.html`
  de REAPER (`remaster/reaper/render_stats.py`).
- Claves de perfil `[loudness] peak_ceiling_dbfs`, `verify_tolerance_db` y
  `verify_item_tolerance_db`.

### Fixed
- La protección de pico medía el pico sobre el downmix a mono, que esconde
  hasta 6 dB de un transitorio presente en un solo canal. Medido en
  `es_vocals.flac` de s03e01: pico real -2.6 dBFS, pico del mono -5.2 dBFS.
  Ese error de 2.6 dB llegaba entero a la ganancia global — que es justo la
  que debía impedir el recorte — y dejaba el render con 0.8 dB de margen
  donde el código creía tener 6. Ahora el pico se mide por frames sobre
  todos los canales (`load_mono_and_peak_frames`), tanto en la ganancia
  global como en la protección por escena.

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
