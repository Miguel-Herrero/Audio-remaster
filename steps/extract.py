import subprocess
import sys
from pathlib import Path

try:
    from . import config
except ImportError:
    import config

def extract_audio(input_mkv: Path, output_dir: Path, language: str, track_index: int = 0) -> Path:
    """
    Extracts an audio track from an MKV file to FLAC.
    
    Args:
        input_mkv: Path to the source MKV
        output_dir: Directory where to save the FLAC
        language: 'es' or 'en' for naming the file
        track_index: Audio track index (0:a:0, 0:a:1, etc.)
    
    Returns:
        Path to the generated FLAC file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Output name based on input and language
    stem = input_mkv.stem
    output_flac = output_dir / f"{stem}_{language}_source.{config.CODEC}"
    
    cmd = [
        config.FFMPEG_PATH,
        "-i", str(input_mkv),
        "-map", f"0:a:{track_index}",
        "-c:a", config.CODEC,
        "-y",  # Overwrite if exists
        str(output_flac)
    ]
    
    print(f"[extract] Extracting {language} audio from {input_mkv.name}...")
    print(f"[extract] Command: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[ERROR] {config.FFMPEG_PATH} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    print(f"[extract] ✓ Audio extracted: {output_flac}")
    return output_flac


if __name__ == "__main__":
    # Quick test if script is run alone
    if len(sys.argv) < 3:
        print("Usage: python extract.py <input.mkv> <output_dir> [language] [track_index]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    lang = sys.argv[3] if len(sys.argv) > 3 else "es"
    track = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    
    extract_audio(input_path, output_path, lang, track)
