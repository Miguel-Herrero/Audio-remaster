import sys
import subprocess
from pathlib import Path
import json

try:
    from . import config
except ImportError:
    import config

def get_video_track_id(mkv_file: Path):
    """
    Retrieves the video track ID from the MKV.
    """
    cmd = [config.MKVMERGE_PATH, "-J", str(mkv_file)]
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
    Creates a new MKV by combining:
    - Video from 4K file
    - All audio and subtitle tracks from base MKV
    - Optional external subtitles
    """
    
    print(f"[remux] === 4K REMUX ===")
    print(f"[remux] Base MKV (audio/subs): {base_mkv.name}")
    print(f"[remux] 4K MKV (video): {video_4k_mkv.name}")
    if external_subs:
        print(f"[remux] External subtitles: {external_subs.name}")
    print(f"[remux] Output: {output_file.name}")
    print(f"[remux]")
    
    # Analyze base MKV
    print(f"[remux] Analyzing base MKV...")
    cmd = [config.MKVMERGE_PATH, "-J", str(base_mkv)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[remux] ERROR: Could not analyze base MKV")
        sys.exit(1)
    
    base_info = json.loads(result.stdout)
    
    # Get audio tracks from base MKV
    audio_tracks = []
    for track in base_info.get("tracks", []):
        if track["type"] == "audio":
            audio_tracks.append(track["id"])
            lang = track["properties"].get("language", "und")
            codec = track.get("codec", "unknown")
            print(f"[remux]   Audio {track['id']}: {lang} ({codec})")
    
    # Get subtitle tracks from base MKV
    subtitle_tracks = []
    for track in base_info.get("tracks", []):
        if track["type"] == "subtitles":
            subtitle_tracks.append(track["id"])
            lang = track["properties"].get("language", "und")
            print(f"[remux]   Subtitle {track['id']}: {lang}")
    
    # Get title from base MKV
    title = base_info.get("container", {}).get("properties", {}).get("title", None)
    
    # Analyze 4K MKV
    print(f"[remux]")
    print(f"[remux] Analyzing 4K MKV...")
    cmd = [config.MKVMERGE_PATH, "-J", str(video_4k_mkv)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[remux] ERROR: Could not analyze 4K MKV")
        sys.exit(1)
    
    video_4k_info = json.loads(result.stdout)
    
    # Get 4K video track
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
        print(f"[remux] ERROR: Video track not found in 4K MKV")
        sys.exit(1)
    
    # Build mkvmerge command
    print(f"[remux]")
    print(f"[remux] Building final MKV...")
    
    cmd = [
        config.MKVMERGE_PATH,
        "-o", str(output_file)
    ]
    
    # Title (copy from base)
    if title:
        # Modify title to indicate 4K
        title_4k = title.replace("(1984)", "(1984) [4K]") if "(1984)" in title else f"{title} [4K]"
        cmd.extend(["--title", title_4k])
    
    # Video from 4K MKV (video only, no audio or subtitles)
    cmd.extend([
        "--video-tracks", str(video_track),
        "--no-audio",
        "--no-subtitles",
        "--disable-track-statistics-tags",
        str(video_4k_mkv)
    ])
    
    # Audio and subtitles from base MKV (no video)
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
    
    # Track order: video first, then audio, then subtitles
    track_order = ["0:0"]  # Video from 4K file
    
    # Audio from base (file 1)
    for audio_id in audio_tracks:
        track_order.append(f"1:{audio_id}")
    
    # Subtitles from base (file 1)
    for subtitle_id in subtitle_tracks:
        track_order.append(f"1:{subtitle_id}")
    
    # External subtitles (if provided)
    file_index = 2
    if external_subs:
        print(f"[remux] Adding external subtitles...")
        cmd.extend([
            "--language", "0:eng",
            "--track-name", "0:English (4K)",
            "--default-track-flag", "0:0",
            "--forced-display-flag", "0:0",
            str(external_subs)
        ])
        track_order.append(f"{file_index}:0")
    
    cmd.extend(["--track-order", ",".join(track_order)])
    
    # Execute mkvmerge
    print(f"[remux] Running mkvmerge...")
    print(f"[remux]")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"[remux] ERROR: mkvmerge failed")
        print(f"[remux] STDERR: {result.stderr}")
        print(f"[remux] STDOUT: {result.stdout}")
        sys.exit(1)
    
    print(f"[remux]")
    print(f"[remux] OK: 4K MKV created")
    print(f"[remux] File: {output_file}")
    
    # Show final file info
    print(f"[remux]")
    print(f"[remux] Final file information:")
    cmd_info = [config.MKVMERGE_PATH, "-i", str(output_file)]
    subprocess.run(cmd_info)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"[remux] Sherlock Audio Pipeline - 4K Remux")
        print(f"[remux]")
        print(f"[remux] Creates a new 4K MKV combining:")
        print(f"[remux]   - Video from 4K file")
        print(f"[remux]   - All audio from base MKV (ES MIX, EN, JA)")
        print(f"[remux]   - All subtitles from base MKV")
        print(f"[remux]   - Optional external subtitles (SRT, ASS, etc.)")
        print(f"[remux]")
        print(f"[remux] Usage:")
        print(f"[remux]   python3 steps/remux_4k.py <base_mkv> <4k_video_mkv> <output_4k.mkv> [external_subs.srt]")
        print(f"[remux]")
        print(f"[remux] Example:")
        print(f"[remux]   python3 steps/remux_4k.py \\")
        print(f'[remux]     "workspace/output/Sherlock Holmes.s01e06.mkv" \\')
        print(f'[remux]     "/Users/.../00_sources/Sherlock Holmes.s01e06.4K.mkv" \\')
        print(f'[remux]     "workspace/output/Sherlock Holmes.s01e06 [4K].mkv" \\')
        print(f'[remux]     "/Users/.../00_sources/Sherlock Holmes.s01e06.4K.eng.srt"')
        sys.exit(1)
    
    base_mkv_path = Path(sys.argv[1])
    video_4k_mkv_path = Path(sys.argv[2])
    output_file_path = Path(sys.argv[3])
    external_subs_path = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    
    if not base_mkv_path.exists():
        print(f"[remux] ERROR: File does not exist: {base_mkv_path}")
        sys.exit(1)
    
    if not video_4k_mkv_path.exists():
        print(f"[remux] ERROR: File does not exist: {video_4k_mkv_path}")
        sys.exit(1)
    
    if external_subs_path and not external_subs_path.exists():
        print(f"[remux] ERROR: Subtitle file does not exist: {external_subs_path}")
        sys.exit(1)
    
    # Create output directory if it doesn't exist
    output_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"[remux] Sherlock Audio Pipeline - 4K Remux")
    print(f"[remux]")
    
    remux_4k(base_mkv_path, video_4k_mkv_path, output_file_path, external_subs_path)
