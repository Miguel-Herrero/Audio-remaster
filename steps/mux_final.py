import sys
import subprocess
from pathlib import Path
import json

try:
    from . import config
except ImportError:
    import config

def extract_audio_tracks(mkv_file: Path, output_dir: Path):
    """
    Extracts audio tracks from the original MKV and converts them to FLAC.
    Returns a dict with paths to the generated files.
    """
    print(f"[mux] Analyzing audio tracks from the original MKV...")
    
    # Get MKV info
    cmd = [config.MKVMERGE_PATH, "-J", str(mkv_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[mux] ERROR: Could not analyze MKV")
        print(f"[mux] Command: {' '.join(cmd)}")
        print(f"[mux] Return code: {result.returncode}")
        print(f"[mux] STDOUT: {result.stdout}")
        print(f"[mux] STDERR: {result.stderr}")
        sys.exit(1)
    
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"[mux] ERROR: Could not parse JSON output from mkvmerge")
        print(f"[mux] Error: {e}")
        print(f"[mux] Output: {result.stdout[:500]}")
        sys.exit(1)
    
    audio_tracks = {}
    
    # Find audio tracks by language
    for track in info.get("tracks", []):
        if track["type"] == "audio":
            lang = track["properties"].get("language", "und")
            track_id = track["id"]
            codec = track.get("codec", "unknown")
            
            print(f"[mux]   Track {track_id}: {lang} ({codec})")
            
            if lang in ["eng", "jpn"]:
                audio_tracks[lang] = {
                    "id": track_id,
                    "codec": codec
                }
    
    if not audio_tracks:
        print(f"[mux] WARNING: No English or Japanese audio tracks found")
        return {}
    
    # Extract and convert to FLAC
    extracted_files = {}
    
    for lang, track_info in audio_tracks.items():
        print(f"[mux] Extracting {lang} audio...")
        
        # Extract track
        temp_file = output_dir / f"temp_audio_{lang}.mka"
        cmd = [
            config.MKVEXTRACT_PATH,
            str(mkv_file),
            "tracks",
            f"{track_info['id']}:{temp_file}"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[mux] ERROR: Could not extract {lang} audio")
            print(f"[mux] STDERR: {result.stderr}")
            continue
        
        # Convert to FLAC
        flac_file = output_dir / f"{lang}.{config.CODEC}"
        print(f"[mux] Converting {lang} to {config.CODEC}...")
        
        cmd = [
            config.FFMPEG_PATH,
            "-i", str(temp_file),
            "-c:a", config.CODEC,
            "-compression_level", "8",
            "-y",
            str(flac_file)
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            print(f"[mux] ERROR: Could not convert {lang} to FLAC")
            temp_file.unlink()
            continue
        
        # Clean up temporary file
        temp_file.unlink()
        
        extracted_files[lang] = flac_file
        print(f"[mux] OK: {lang}.flac created")
    
    return extracted_files


def get_subtitle_tracks(mkv_file: Path):
    """
    Retrieves subtitle track information from the MKV.
    """
    cmd = [config.MKVMERGE_PATH, "-J", str(mkv_file)]
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
    Creates the final MKV with all audio and subtitle tracks.
    """
    
    print(f"[mux] === FINAL MUX ===")
    print(f"[mux] Source video: {mkv_source.name}")
    print(f"[mux] ES MIX audio: {es_mix.name}")
    print(f"[mux] Output: {output_file.name}")
    print(f"[mux]")
    
    # Create temporary working directory
    work_dir = output_file.parent / "mux_temp"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract original audios
    audio_files = extract_audio_tracks(mkv_source, work_dir)
    
    # Get subtitle tracks
    subtitle_tracks = get_subtitle_tracks(mkv_source)
    print(f"[mux] Subtitles found: {len(subtitle_tracks)}")
    
    # Build mkvmerge command
    cmd = [
        config.MKVMERGE_PATH,
        "-o", str(output_file)
    ]
    
    # Title
    if title:
        cmd.extend(["--title", title])
    
    # Video source (video and subtitles only)
    cmd.extend([
        "--video-tracks", "0",
        "--no-audio",
        "--disable-track-statistics-tags"
    ])
    
    # Configure subtitles if any
    if subtitle_tracks:
        subtitle_ids = ",".join(str(t) for t in subtitle_tracks)
        cmd.extend(["--subtitle-tracks", subtitle_ids])
        
        # Set flags for each subtitle
        for track_id in subtitle_tracks:
            cmd.extend([
                f"--forced-display-flag", f"{track_id}:0",
                f"--default-track-flag", f"{track_id}:0"
            ])
    
    cmd.append(str(mkv_source))
    
    # Audio ES (Spanish, default)
    cmd.extend([
        "--language", f"0:{config.LANG_ES}",
        "--default-track-flag", "0:1",
        "--disable-track-statistics-tags",
        "--track-name", f"0:{config.TRACK_ES_VOX} (Reconstructed)",
        str(es_mix)
    ])
    
    # Track order (build dynamically)
    track_order = ["0:0"]  # Video
    track_index = 1
    
    # Audio EN (English, original)
    if config.LANG_EN in audio_files:
        cmd.extend([
            "--language", f"0:{config.LANG_EN}",
            "--default-track-flag", "0:0",
            "--disable-track-statistics-tags",
            "--original-flag", "0:1",
            str(audio_files[config.LANG_EN])
        ])
        track_order.append(f"{track_index+1}:0")
        track_index += 1
    
    track_order.append(f"{track_index}:0")  # ES MIX
    track_index += 1
    
    # Audio JA (Japanese)
    if config.LANG_JA in audio_files:
        cmd.extend([
            "--language", f"0:{config.LANG_JA}",
            "--default-track-flag", "0:0",
            "--disable-track-statistics-tags",
            str(audio_files[config.LANG_JA])
        ])
        track_order.append(f"{track_index+1}:0")
    
    # Add subtitles to track order
    for track_id in subtitle_tracks:
        track_order.append(f"0:{track_id}")
    
    # Add track order
    cmd.extend(["--track-order", ",".join(track_order)])
    
    # Execute mkvmerge
    print(f"[mux]")
    print(f"[mux] Running mkvmerge...")
    print(f"[mux]")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[mux] ERROR: mkvmerge failed")
        print(f"[mux] STDERR: {result.stderr}")
        print(f"[mux] STDOUT: {result.stdout}")
        sys.exit(1)
    
    # Clean up temporary files
    print(f"[mux] Cleaning up temporary files...")
    for audio_file in audio_files.values():
        if audio_file.exists():
            audio_file.unlink()
    if work_dir.exists() and not list(work_dir.iterdir()):
        work_dir.rmdir()
    
    print(f"[mux]")
    print(f"[mux] OK: Final MKV created")
    print(f"[mux] File: {output_file}")
    
    # Show final file info
    print(f"[mux]")
    print(f"[mux] Final file information:")
    cmd_info = [config.MKVMERGE_PATH, "-i", str(output_file)]
    subprocess.run(cmd_info)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"[mux] Sherlock Audio Pipeline - Final Mux")
        print(f"[mux]")
        print(f"[mux] Creates the final MKV with:")
        print(f"[mux]   - Original video")
        print(f"[mux]   - Rendered ES MIX audio (default)")
        print(f"[mux]   - Original EN audio converted to FLAC (original flag)")
        print(f"[mux]   - Original JA audio converted to FLAC (if exists)")
        print(f"[mux]   - Original subtitles")
        print(f"[mux]")
        print(f"[mux] Usage:")
        print(f"[mux]   python3 steps/mux_final.py <mkv_source> <es_mix.flac> <output.mkv> [title]")
        print(f"[mux]")
        print(f"[mux] Example:")
        print(f"[mux]   python3 steps/mux_final.py \\")
        print(f'[mux]     "/Users/.../00_sources/Sherlock Holmes.s01e06.mkv" \\')
        print(f'[mux]     "workspace/output/s01e06.flac" \\')
        print(f'[mux]     "/Users/.../workspace/output/Sherlock Holmes.s01e06.mkv" \\')
        print(f'[mux]     "Sherlock Holmes (1984) S01E06: The Dying Detective"')
        sys.exit(1)
    
    mkv_source_path = Path(sys.argv[1])
    es_mix_path = Path(sys.argv[2])
    output_file_path = Path(sys.argv[3])
    title = sys.argv[4] if len(sys.argv) > 4 else None
    
    if not mkv_source_path.exists():
        print(f"[mux] ERROR: File does not exist: {mkv_source_path}")
        sys.exit(1)
    
    if not es_mix_path.exists():
        print(f"[mux] ERROR: File does not exist: {es_mix_path}")
        sys.exit(1)
    
    # Create output directory if it doesn't exist
    output_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"[mux] Sherlock Audio Pipeline - Final Mux")
    print(f"[mux]")
    
    mux_final(mkv_source_path, es_mix_path, output_file_path, title)
