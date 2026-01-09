import sys
import subprocess
from pathlib import Path
import json


def get_video_track_id(mkv_file: Path):
    """
    Obtiene el ID de la pista de video del MKV.
    """
    cmd = ["/opt/homebrew/bin/mkvmerge", "-J", str(mkv_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return None
    
    info = json.loads(result.stdout)
    
    for track in info.get("tracks", []):
        if track["type"] == "video":
            return track["id"]
    
    return None


def remux_4k(base_mkv: Path, video_4k_mkv: Path, output_file: Path, external_subs: Path = None):
    """
    Crea un nuevo MKV combinando:
    - Video del archivo 4K
    - Todos los audios y subtítulos del MKV base
    - Subtítulos externos opcionales
    """
    
    print(f"[remux] === REMUX 4K ===")
    print(f"[remux] MKV base (audios/subs): {base_mkv.name}")
    print(f"[remux] MKV 4K (video): {video_4k_mkv.name}")
    if external_subs:
        print(f"[remux] Subtítulos externos: {external_subs.name}")
    print(f"[remux] Salida: {output_file.name}")
    print(f"[remux]")
    
    # Analizar MKV base
    print(f"[remux] Analizando MKV base...")
    cmd = ["/opt/homebrew/bin/mkvmerge", "-J", str(base_mkv)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[remux] ERROR: No se pudo analizar el MKV base")
        sys.exit(1)
    
    base_info = json.loads(result.stdout)
    
    # Obtener pistas de audio del MKV base
    audio_tracks = []
    for track in base_info.get("tracks", []):
        if track["type"] == "audio":
            audio_tracks.append(track["id"])
            lang = track["properties"].get("language", "und")
            codec = track.get("codec", "unknown")
            print(f"[remux]   Audio {track['id']}: {lang} ({codec})")
    
    # Obtener pistas de subtítulos del MKV base
    subtitle_tracks = []
    for track in base_info.get("tracks", []):
        if track["type"] == "subtitles":
            subtitle_tracks.append(track["id"])
            lang = track["properties"].get("language", "und")
            print(f"[remux]   Subtítulo {track['id']}: {lang}")
    
    # Obtener título del MKV base
    title = base_info.get("container", {}).get("properties", {}).get("title", None)
    
    # Analizar MKV 4K
    print(f"[remux]")
    print(f"[remux] Analizando MKV 4K...")
    cmd = ["/opt/homebrew/bin/mkvmerge", "-J", str(video_4k_mkv)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[remux] ERROR: No se pudo analizar el MKV 4K")
        sys.exit(1)
    
    video_4k_info = json.loads(result.stdout)
    
    # Obtener pista de video 4K
    video_track = None
    for track in video_4k_info.get("tracks", []):
        if track["type"] == "video":
            video_track = track["id"]
            codec = track.get("codec", "unknown")
            width = track["properties"].get("pixel_dimensions", "").split("x")[0] if "pixel_dimensions" in track["properties"] else "unknown"
            height = track["properties"].get("pixel_dimensions", "").split("x")[1] if "pixel_dimensions" in track["properties"] else "unknown"
            print(f"[remux]   Video {track['id']}: {width}x{height} ({codec})")
            break
    
    if video_track is None:
        print(f"[remux] ERROR: No se encontró pista de video en el MKV 4K")
        sys.exit(1)
    
    # Construir comando mkvmerge
    print(f"[remux]")
    print(f"[remux] Construyendo MKV final...")
    
    cmd = [
        "/opt/homebrew/bin/mkvmerge",
        "-o", str(output_file)
    ]
    
    # Título (copiar del base)
    if title:
        # Modificar título para indicar que es 4K
        title_4k = title.replace("(1984)", "(1984) [4K]") if "(1984)" in title else f"{title} [4K]"
        cmd.extend(["--title", title_4k])
    
    # Video del MKV 4K (solo video, no audio ni subtítulos)
    cmd.extend([
        "--video-tracks", str(video_track),
        "--no-audio",
        "--no-subtitles",
        "--disable-track-statistics-tags",
        str(video_4k_mkv)
    ])
    
    # Audios y subtítulos del MKV base (sin video)
    audio_ids = ",".join(str(t) for t in audio_tracks)
    
    cmd.extend([
        "--no-video",
        "--audio-tracks", audio_ids,
        "--disable-track-statistics-tags"
    ])
    
    if subtitle_tracks:
        subtitle_ids = ",".join(str(t) for t in subtitle_tracks)
        cmd.extend(["--subtitle-tracks", subtitle_ids])
    
    cmd.append(str(base_mkv))
    
    # Track order: video primero, luego audios, luego subtítulos
    track_order = ["0:0"]  # Video del archivo 4K
    
    # Audios del base (archivo 1)
    for audio_id in audio_tracks:
        track_order.append(f"1:{audio_id}")
    
    # Subtítulos del base (archivo 1)
    for subtitle_id in subtitle_tracks:
        track_order.append(f"1:{subtitle_id}")
    
    # Subtítulos externos (si se proporcionan)
    file_index = 2
    if external_subs:
        print(f"[remux] Añadiendo subtítulos externos...")
        cmd.extend([
            "--language", "0:eng",
            "--track-name", "0:English (4K)",
            "--default-track-flag", "0:0",
            "--forced-display-flag", "0:0",
            str(external_subs)
        ])
        track_order.append(f"{file_index}:0")
    
    cmd.extend(["--track-order", ",".join(track_order)])
    
    # Ejecutar mkvmerge
    print(f"[remux] Ejecutando mkvmerge...")
    print(f"[remux]")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[remux] ERROR: mkvmerge falló")
        print(f"[remux] STDERR: {result.stderr}")
        print(f"[remux] STDOUT: {result.stdout}")
        sys.exit(1)
    
    print(f"[remux]")
    print(f"[remux] OK: MKV 4K creado")
    print(f"[remux] Archivo: {output_file}")
    
    # Mostrar info del archivo final
    print(f"[remux]")
    print(f"[remux] Información del archivo final:")
    cmd_info = ["/opt/homebrew/bin/mkvmerge", "-i", str(output_file)]
    subprocess.run(cmd_info)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"[remux] Sherlock Audio Pipeline - Remux 4K")
        print(f"[remux]")
        print(f"[remux] Crea un nuevo MKV 4K combinando:")
        print(f"[remux]   - Video del archivo 4K")
        print(f"[remux]   - Todos los audios del MKV base (ES MIX, EN, JA)")
        print(f"[remux]   - Todos los subtítulos del MKV base")
        print(f"[remux]   - Subtítulos externos opcionales (SRT, ASS, etc.)")
        print(f"[remux]")
        print(f"[remux] Uso:")
        print(f"[remux]   python3 steps/remux_4k.py <mkv_base> <mkv_4k_video> <output_4k.mkv> [subtitulos.srt]")
        print(f"[remux]")
        print(f"[remux] Ejemplo:")
        print(f"[remux]   python3 steps/remux_4k.py \\")
        print(f'[remux]     "workspace/output/Sherlock Holmes.s01e06.mkv" \\')
        print(f'[remux]     "/Users/.../00_sources/Sherlock Holmes.s01e06.4K.mkv" \\')
        print(f'[remux]     "workspace/output/Sherlock Holmes.s01e06 [4K].mkv" \\')
        print(f'[remux]     "/Users/.../00_sources/Sherlock Holmes.s01e06.4K.eng.srt"')
        sys.exit(1)
    
    base_mkv = Path(sys.argv[1])
    video_4k_mkv = Path(sys.argv[2])
    output_file = Path(sys.argv[3])
    external_subs = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    
    if not base_mkv.exists():
        print(f"[remux] ERROR: No existe el archivo: {base_mkv}")
        sys.exit(1)
    
    if not video_4k_mkv.exists():
        print(f"[remux] ERROR: No existe el archivo: {video_4k_mkv}")
        sys.exit(1)
    
    if external_subs and not external_subs.exists():
        print(f"[remux] ERROR: No existe el archivo de subtítulos: {external_subs}")
        sys.exit(1)
    
    # Crear directorio de salida si no existe
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"[remux] Sherlock Audio Pipeline - Remux 4K")
    print(f"[remux]")
    
    remux_4k(base_mkv, video_4k_mkv, output_file, external_subs)
