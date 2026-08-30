# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado con [SemVer](https://semver.org/lang/es/).

## [2.2.0] - 2026-08-31

### Added
- La UI web se rehace entera. La anterior era un andamio: una tabla, dos
  desplegables con los nombres tecnicos de los pasos y un `<pre>` con el log
  crudo. No se podia saber que iba a hacer «Ejecutar» sin haber escrito el
  pipeline.
- **Timeline** como pieza central: un nodo numerado por paso, unidos por una
  linea punteada que se vuelve solida al completarse, de modo que el
  recorrido hace de barra de progreso. Cada nodo lleva su estado (pendiente,
  en curso, hecho, sin cambios, omitido con motivo, error), su duracion, que
  hace el paso y **que salvaguarda tiene**. El rango a ejecutar se elige
  pulsando nodos (clic = uno; mayusculas = rango), en vez de con dos
  desplegables.
- El estado inicial sale del manifiesto, asi que el timeline esta poblado
  nada mas abrir el episodio. Un paso con varias entradas (`extract:es`,
  `extract:en`, `separate:en`…) cuenta como uno solo y suma duraciones;
  `ingest` y `verify`, que no escriben en el manifiesto, se deducen de lo que
  dejan en disco.
- **La carpeta base se elige desde el navegador** (`GET`/`POST /api/root`) y se
  recuerda entre arranques en `~/.config/remaster/ui.json`, junto con las
  ultimas usadas. Se puede escribir la ruta o arrastrar la carpeta desde el
  Finder. Ya no hace falta `REMASTER_MOVIES_DIR` ni `--movies-dir`, que pasan
  a fijar solo por donde se empieza la primera vez.
- **El token de MVSEP se mete en la UI**, no en una variable de entorno. Se lee
  de `.env` al arrancar y se puede guardar ahi con un check. La API nunca
  devuelve el token, solo si esta puesto y de donde sale; al ejecutar se pasa
  al subproceso por entorno y nunca por argumentos, que `ps` enseña a
  cualquiera. Nuevo `remaster/web/envfile.py`: escribir una clave preserva
  comentarios y orden del fichero. `.env` va a `.gitignore`.
- **Paneles contextuales**: la calibracion manual y el re-analisis dejan de ser
  secciones sueltas siempre visibles y aparecen bajo el nodo del paso al que
  pertenecen, solo cuando ese paso esta en el rango elegido. La calibracion
  ademas solo sale si `align` ya ha corrido — antes no hay nada que corregir.
  El nombre del fichero final (`--name`) sale bajo `mux`, con vista previa.
- **Las salvaguardas se ven antes de pulsar.** Se extrae de `_guard_hand_edits`
  un `project_hand_edited()` reutilizable; la lista de episodios lo expone y
  la UI marca «editado a mano». Si el rango incluye `project` con el `.RPP`
  tocado, el nodo se pinta en rojo con la alternativa, en vez de que te
  enteres por el error al intentarlo.
- `remaster run --events <fichero>`: traza NDJSON con una linea por transicion
  de paso (`remaster/events.py`). La UI la usa para el timeline en vez de
  parsear la salida de `rich`, que esta pensada para leerse, no para
  maquinas. Sin la opcion es un no-op y la CLI se comporta igual que antes.
- `remaster/steps_meta.py`: que hace cada paso y que protege, en una linea.
  Fuente unica — `STEP_ORDER` se deriva de ahi, asi que no se puede añadir un
  paso sin explicarlo. Se sirve por `GET /api/steps`.
- Los episodios pasan de filas de tabla a tarjetas con anillo de progreso y
  distintivos de lo que hay hecho («editado a mano», informe, MKV). Los mismos
  distintivos, con enlaces al informe y al grafico de sync, encabezan el
  episodio abierto.
- El rango que se propone al abrir un episodio es «lo que falta»; si no falta
  nada, `verify` a secas, que es el bucle de trabajo real (editar en REAPER ⇄
  revisar). Antes se seleccionaban los once pasos y se abrian los cuatro
  paneles a la vez, que es el amontonamiento del que se venia.
- Tema claro/oscuro con interruptor (oscuro por defecto), enlace directo a un
  episodio por `#codigo`, atajos de teclado, avisos al terminar y textos
  utiles cuando no hay nada que enseñar.
- `tests/test_ui_contract.py`: aqui no hay tests de JS, pero los puntos donde
  el HTML y el paquete acuerdan un vocabulario si se comprueban leyendo el
  fichero, y son justo donde las cosas se rompen sin fallar. Todo `outcome`
  que emita la traza tiene que tener estado visual, y todo estado visual su
  glifo y su regla CSS.

### Changed
- **`verify` pasa a ser un paso mas**, entre `project` y `render`, que es
  justo donde cae: entre generar el proyecto y renderizarlo esta el rato en
  el que lo editas a mano. Nunca corta el pipeline — es un informe, no una
  puerta. Sin medida de REAPER se omite diciendo por que, en vez de dar un
  analisis parcial sin avisar; el analisis parcial sigue estando en
  `remaster verify --no-stats`, pero pedirlo es ahora una decision explicita.
  Desaparece de la UI el check «sin medir».
- El log de la UI ya no sale con codigos ANSI en crudo (`[1mverify[0m`):
  al subproceso se le pide que no coloree y se le da mas ancho que los 80
  que asume al no hablar con un terminal.

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
- Informe en `.remaster/verify.json` y `.remaster/verify.html`. Empieza por
  «Qué tengo que hacer»: cada hallazgo traducido a una instrucción concreta
  en lenguaje llano (qué pasa, por qué importa y los pasos exactos en
  REAPER, con los tramos y los dB de cada uno). El detalle técnico queda
  debajo, plegado. Detecta además si el proyecto se guardó después de la
  última medida y, en ese caso, lo primero que pide es volver a medir.
- Botón «Re-analizar tras sincronizar a mano» en la UI web, con el estado de
  la última medida detectada y las instrucciones para obtenerla (en REAPER:
  seleccionar las pistas y ejecutar la acción «Calculate loudness of selected
  tracks via dry run render»).
- Lector de `.RPP` (`remaster/reaper/rpp_read.py`) y de `render_stats.html`
  de REAPER (`remaster/reaper/render_stats.py`).
- Claves de perfil `[loudness] peak_ceiling_dbfs`, `verify_tolerance_db` y
  `verify_item_tolerance_db`.

### Changed
- `<ep>_render.RPP` pasa a **derivarse** de `<ep>.RPP` en el paso `render`,
  en vez de escribirse aparte en el paso `project`.
- El nombre del fichero final se pasa entero, sin interpolar nada:
  `--name "Las aventuras de Sherlock Holmes (1984) S02E06 El problema final"`.
  Sustituye a `--episode-title` y `--series-title`, y deja sin uso la clave
  `series_title` del perfil (eliminada). Antes se componía como
  `<serie> <SxxExx> <título>`, lo que fijaba tanto el orden como la
  presencia de las tres partes: no había forma de cambiar el nombre de la
  serie sin tocar el TOML, ni de nombrar una película. Ahora `naming.py`
  solo añade los corchetes (`[1080p AVC] [FLAC ...] [Subs ...]`), que sí
  salen de lo detectado. Sin `--name` se sigue conservando el nombre del
  Blu-ray tal cual; el título interno del MKV, si no se da `--title`,
  pasa a ser el propio `--name`.

### Fixed
- El paso `project` ya no puede sobrescribir un `<ep>.RPP` editado a mano.
  El riesgo era real y no hipotético: la ganancia global es uno de los
  parámetros del paso y cambia sola al recalcular `loudness`, así que
  bastaba con eso para que el paso se considerase desactualizado y
  reescribiera el proyecto encima de horas de sync manual. Ahora compara el
  hash del fichero con el que el manifiesto registró al escribirlo y, si no
  coincide, aborta explicando cómo continuar. Con `--force` procede, pero
  guardando antes una copia con marca de tiempo.
- El render usaba un `<ep>_render.RPP` congelado en el momento de generarlo,
  así que ignoraba por completo las ediciones manuales. Medido en s03e01:
  352 items en el proyecto editado frente a 25 en el de render, con día y
  medio de trabajo entre uno y otro. Ahora se deriva del editado justo antes
  de renderizar, copiándolo línea a línea y forzando solo el destino/formato
  de render y el mute de las pistas que no deben sonar — de modo que una
  pista nueva que añada el usuario entra en el render. No se renderiza el
  editado directamente porque lleva los ajustes del diálogo de Render que
  haya dejado el usuario (en s03e01 apuntaban a `hudson_risa.flac`).
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
