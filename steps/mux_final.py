import sys
import subprocess
from pathlib import Path
import json


def extract_audio_tracks(mkv_file: Path, output_dir: Path):
    """
    Extrae las pistas de audio del MKV original y las convierte a FLAC.
    Retorna dict con las rutas de los archivos generados.
    """
    print(f"[mux] Analizando pistas de audio del MKV original...")
    
    # Obtener info del MKV
    cmd = ["/opt/homebrew/bin/mkvmerge", "-J", str(mkv_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[mux] ERROR: No se pudo analizar el MKV")
        print(f"[mux] Comando: {' '.join(cmd)}")
        print(f"[mux] Return code: {result.returncode}")
        print(f"[mux] STDOUT: {result.stdout}")
        print(f"[mux] STDERR: {result.stderr}")
        sys.exit(1)
    
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"[mux] ERROR: No se pudo parsear la salida JSON de mkvmerge")
        print(f"[mux] Error: {e}")
        print(f"[mux] Output: {result.stdout[:500]}")
        sys.exit(1)
    
    audio_tracks = {}
    
    # Buscar pistas de audio por idioma
    for track in info.get("tracks", []):
        if track["type"] == "audio":
            lang = track["properties"].get("language", "und")
            track_id = track["id"]
            codec = track.get("codec", "unknown")
            
            print(f"[mux]   Pista {track_id}: {lang} ({codec})")
            
            if lang in ["eng", "jpn"]:
                audio_tracks[lang] = {
                    "id": track_id,
                    "codec": codec
                }
    
    if not audio_tracks:
        print(f"[mux] ADVERTENCIA: No se encontraron pistas de audio en inglés o japonés")
        return {}
    
    # Extraer y convertir a FLAC
    extracted_files = {}
    
    for lang, track_info in audio_tracks.items():
        print(f"[mux] Extrayendo audio {lang}...")
        
        # Extraer pista
        temp_file = output_dir / f"temp_audio_{lang}.mka"
        cmd = [
            "/opt/homebrew/bin/mkvextract",
            str(mkv_file),
            "tracks",
            f"{track_info['id']}:{temp_file}"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[mux] ERROR: No se pudo extraer audio {lang}")
            print(f"[mux] STDERR: {result.stderr}")
            continue
        
        # Convertir a FLAC
        flac_file = output_dir / f"{lang}.flac"
        print(f"[mux] Convirtiendo {lang} a FLAC...")
        
        cmd = [
            "ffmpeg",
            "-i", str(temp_file),
            "-c:a", "flac",
            "-compression_level", "8",
            "-y",
            str(flac_file)
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            print(f"[mux] ERROR: No se pudo convertir {lang} a FLAC")
            temp_file.unlink()
            continue
        
        # Limpiar temporal
        temp_file.unlink()
        
        extracted_files[lang] = flac_file
        print(f"[mux] OK: {lang}.flac creado")
    
    return extracted_files


def get_subtitle_tracks(mkv_file: Path):
    """
    Obtiene información de las pistas de subtítulos del MKV.
    """
    cmd = ["/opt/homebrew/bin/mkvmerge", "-J", str(mkv_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return []
    
    info = json.loads(result.stdout)
    
    subtitle_tracks = []
    for track in info.get("tracks", []):
        if track["type"] == "subtitles":
            subtitle_tracks.append(track["id"])
    
    return subtitle_tracks


def mux_final(mkv_source: Path, es_mix: Path, output_file: Path, title: str = None):
    """
    Crea el MKV final con todos los audios y subtítulos.
    """
    
    print(f"[mux] === MUX FINAL ===")
    print(f"[mux] Video fuente: {mkv_source.name}")
    print(f"[mux] Audio ES MIX: {es_mix.name}")
    print(f"[mux] Salida: {output_file.name}")
    print(f"[mux]")
    
    # Crear directorio de trabajo temporal
    work_dir = output_file.parent / "mux_temp"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Extraer audios originales
    audio_files = extract_audio_tracks(mkv_source, work_dir)
    
    # Obtener pistas de subtítulos
    subtitle_tracks = get_subtitle_tracks(mkv_source)
    print(f"[mux] Subtítulos encontrados: {len(subtitle_tracks)}")
    
    # Construir comando mkvmerge
    cmd = [
        "/opt/homebrew/bin/mkvmerge",
        "-o", str(output_file)
    ]
    
    # Título
    if title:
        cmd.extend(["--title", title])
    
    # Video source (solo video y subtítulos)
    cmd.extend([
        "--video-tracks", "0",
        "--no-audio",
        "--disable-track-statistics-tags"
    ])
    
    # Configurar subtítulos si existen
    if subtitle_tracks:
        subtitle_ids = ",".join(str(t) for t in subtitle_tracks)
        cmd.extend(["--subtitle-tracks", subtitle_ids])
        
        # Configurar flags para cada subtítulo
        for track_id in subtitle_tracks:
            cmd.extend([
                f"--forced-display-flag", f"{track_id}:0",
                f"--default-track-flag", f"{track_id}:0"
            ])
    
    cmd.append(str(mkv_source))
    
    # Audio ES (español, default)
    cmd.extend([
        "--language", "0:spa",
        "--default-track-flag", "0:1",
        "--disable-track-statistics-tags",
        "--track-name", "0:Audio reconstruido",
        str(es_mix)
    ])
    
    # Track order (construir dinámicamente)
    track_order = ["0:0"]  # Video
    track_index = 1
    
    # Audio EN (inglés, original)
    if "eng" in audio_files:
        cmd.extend([
            "--language", "0:eng",
            "--default-track-flag", "0:0",
            "--disable-track-statistics-tags",
            "--original-flag", "0:1",
            str(audio_files["eng"])
        ])
        track_order.append(f"{track_index+1}:0")
        track_index += 1
    
    track_order.append(f"{track_index}:0")  # ES MIX
    track_index += 1
    
    # Audio JA (japonés)
    if "jpn" in audio_files:
        cmd.extend([
            "--language", "0:jpn",
            "--default-track-flag", "0:0",
            "--disable-track-statistics-tags",
            str(audio_files["jpn"])
        ])
        track_order.append(f"{track_index+1}:0")
    
    # Añadir subtítulos al track order
    for track_id in subtitle_tracks:
        track_order.append(f"0:{track_id}")
    
    # Añadir track order
    cmd.extend(["--track-order", ",".join(track_order)])
    
    # Ejecutar mkvmerge
    print(f"[mux]")
    print(f"[mux] Ejecutando mkvmerge...")
    print(f"[mux]")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[mux] ERROR: mkvmerge falló")
        print(f"[mux] STDERR: {result.stderr}")
        print(f"[mux] STDOUT: {result.stdout}")
        sys.exit(1)
    
    # Limpiar archivos temporales
    print(f"[mux] Limpiando archivos temporales...")
    for audio_file in audio_files.values():
        if audio_file.exists():
            audio_file.unlink()
    if work_dir.exists() and not list(work_dir.iterdir()):
        work_dir.rmdir()
    
    print(f"[mux]")
    print(f"[mux] OK: MKV final creado")
    print(f"[mux] Archivo: {output_file}")
    
    # Mostrar info del archivo final
    print(f"[mux]")
    print(f"[mux] Información del archivo final:")
    cmd_info = ["/opt/homebrew/bin/mkvmerge", "-i", str(output_file)]
    subprocess.run(cmd_info)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"[mux] Sherlock Audio Pipeline - Mux Final")
        print(f"[mux]")
        print(f"[mux] Crea el MKV final con:")
        print(f"[mux]   - Video original")
        print(f"[mux]   - Audio ES MIX renderizado (default)")
        print(f"[mux]   - Audio EN original convertido a FLAC (original flag)")
        print(f"[mux]   - Audio JA original convertido a FLAC (si existe)")
        print(f"[mux]   - Subtítulos originales")
        print(f"[mux]")
        print(f"[mux] Uso:")
        print(f"[mux]   python3 steps/mux_final.py <mkv_source> <es_mix.flac> <output.mkv> [título]")
        print(f"[mux]")
        print(f"[mux] Ejemplo:")
        print(f"[mux]   python3 steps/mux_final.py \\")
        print(f'[mux]     "/Users/.../00_sources/Sherlock Holmes.s01e06.mkv" \\')
        print(f'[mux]     "workspace/output/s01e06.flac" \\')
        print(f'[mux]     "/Users/.../workspace/output/Sherlock Holmes.s01e06.mkv" \\')
        print(f'[mux]     "Sherlock Holmes (1984) S01E06: The Dying Detective"')
        sys.exit(1)
    
    mkv_source = Path(sys.argv[1])
    es_mix = Path(sys.argv[2])
    output_file = Path(sys.argv[3])
    title = sys.argv[4] if len(sys.argv) > 4 else None
    
    if not mkv_source.exists():
        print(f"[mux] ERROR: No existe el archivo: {mkv_source}")
        sys.exit(1)
    
    if not es_mix.exists():
        print(f"[mux] ERROR: No existe el archivo: {es_mix}")
        sys.exit(1)
    
    # Crear directorio de salida si no existe
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"[mux] Sherlock Audio Pipeline - Mux Final")
    print(f"[mux]")
    
    mux_final(mkv_source, es_mix, output_file, title)
