import subprocess
import sys
from pathlib import Path

def extract_audio(input_mkv: Path, output_dir: Path, language: str, track_index: int = 0) -> Path:
    """
    Extrae una pista de audio de un MKV a FLAC.
    
    Args:
        input_mkv: ruta al MKV fuente
        output_dir: carpeta donde guardar el FLAC
        language: 'es' o 'en' para nombrar archivo
        track_index: índice de pista de audio (0:a:0, 0:a:1, etc.)
    
    Returns:
        Path al archivo FLAC generado
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Nombre de salida basado en entrada y lenguaje
    stem = input_mkv.stem
    output_flac = output_dir / f"{stem}_{language}_source.flac"
    
    cmd = [
        "ffmpeg",
        "-i", str(input_mkv),
        "-map", f"0:a:{track_index}",
        "-c:a", "flac",
        "-y",  # Sobreescribir si existe
        str(output_flac)
    ]
    
    print(f"[extract] Extrayendo audio {language} de {input_mkv.name}...")
    print(f"[extract] Comando: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg falló:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    print(f"[extract] ✓ Audio extraído: {output_flac}")
    return output_flac


if __name__ == "__main__":
    # Test rápido si ejecutas este script solo
    if len(sys.argv) < 3:
        print("Uso: python extract.py <input.mkv> <output_dir> [language] [track_index]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    lang = sys.argv[3] if len(sys.argv) > 3 else "es"
    track = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    
    extract_audio(input_path, output_path, lang, track)
