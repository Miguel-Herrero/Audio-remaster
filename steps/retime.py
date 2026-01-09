import subprocess
import sys
from pathlib import Path

def retime_audio(input_flac: Path, output_dir: Path, target_fps: str = "23.976") -> Path:
    """
    Convierte audio de 25 fps a 23.976 fps (o cualquier otro rate).
    
    Args:
        input_flac: audio fuente en FLAC
        output_dir: carpeta de salida
        target_fps: fps destino (default: "23.976")
    
    Returns:
        Path al archivo retimeado
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Calcular factor de atempo: 23.976 / 25 = 0.95904
    if target_fps == "23.976":
        atempo_factor = "0.95904"
    else:
        # Podrías calcular dinámicamente si necesitas otros rates
        raise ValueError(f"Target fps {target_fps} no soportado aún")
    
    # Nombrar archivo de salida
    stem = input_flac.stem.replace("_source", "")
    output_flac = output_dir / f"{stem}_{target_fps.replace('.', '')}.flac"
    
    cmd = [
        "ffmpeg",
        "-i", str(input_flac),
        "-ar", "48000",
        "-filter:a", f"atempo={atempo_factor}",
        "-y",
        str(output_flac)
    ]
    
    print(f"[retime] Convirtiendo {input_flac.name} a {target_fps} fps...")
    print(f"[retime] Factor atempo: {atempo_factor}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg falló:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    print(f"[retime] ✓ Audio retimeado: {output_flac}")
    return output_flac


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python3 retime.py <input.flac> <output_dir> [target_fps]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    fps = sys.argv[3] if len(sys.argv) > 3 else "23.976"
    
    retime_audio(input_path, output_path, fps)
