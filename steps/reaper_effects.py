import sys
import subprocess
import re
from pathlib import Path

try:
    from . import config
except ImportError:
    import config

try:
    import reapy_boost as rpr
    from reapy_boost import reascript_api as RPR
except ImportError:
    print("[effects] ERROR: reapy_boost not installed")
    sys.exit(1)


def calculate_lufs_ffmpeg(audio_file: Path) -> float:
    """
    Calculates LUFS-I of an audio file using ffmpeg.
    """
    print(f"[effects] Calculating LUFS for {audio_file.name}...")
    
    cmd = [
        config.FFMPEG_PATH,
        "-i", str(audio_file),
        "-af", "loudnorm=print_format=json",
        "-f", "null",
        "-"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr
    
    match = re.search(r'"input_i"\s*:\s*"([^"]+)"', output)
    
    if match:
        try:
            lufs = float(match.group(1))
            return lufs
        except:
            pass
    
    return None


def load_reaeq_preset(track, fx_index, preset_name):
    """
    Loads a ReaEQ preset by name.
    """
    print(f"[effects] Loading preset '{preset_name}' into ReaEQ...")
    
    # TrackFX_SetPreset loads a preset by name
    success = RPR.TrackFX_SetPreset(track.id, fx_index, preset_name)
    
    if success:
        print(f"[effects] OK: Preset '{preset_name}' loaded")
    else:
        print(f"[effects] ERROR: Could not load preset '{preset_name}'")
        print(f"[effects] Verify that the preset exists in Reaper")
    
    return success


def apply_effects_to_project(en_vox_file: Path = None, es_vox_file: Path = None, add_volume: bool = False, add_eq: bool = False, add_reafir: bool = False):
    """
    Applies effects to the project currently open in Reaper.
    """
    
    print(f"[effects] Connecting to Reaper...")
    
    try:
        with rpr.inside_reaper():
            project = rpr.Project()
            
            print(f"[effects] Active project: {project.name if hasattr(project, 'name') else 'Unsaved'}")
            
            # Find tracks by name
            tracks = list(project.tracks)
            
            track_en_vox = None
            track_es_vox = None
            
            for track in tracks:
                if config.TRACK_EN_VOX in track.name:
                    track_en_vox = track
                elif config.TRACK_ES_VOX in track.name:
                    track_es_vox = track
            
            if not track_en_vox:
                print(f"[effects] ERROR: Track '{config.TRACK_EN_VOX}' not found")
                sys.exit(1)
            
            if not track_es_vox:
                print(f"[effects] ERROR: Track '{config.TRACK_ES_VOX}' not found")
                sys.exit(1)
            
            print(f"[effects] OK: Tracks found")
            print(f"[effects]   - {track_en_vox.name}")
            print(f"[effects]   - {track_es_vox.name}")
            
            # === VOLUME ADJUSTMENT ===
            if add_volume and en_vox_file and es_vox_file:
                print(f"[effects]")
                print(f"[effects] === LUFS NORMALIZATION ===")
                
                lufs_en = calculate_lufs_ffmpeg(en_vox_file)
                lufs_es = calculate_lufs_ffmpeg(es_vox_file)
                
                if not lufs_en:
                    print(f"[effects] ERROR: Could not calculate LUFS for EN VOX")
                    sys.exit(1)
                
                if not lufs_es:
                    print(f"[effects] ERROR: Could not calculate LUFS for ES VOX")
                    sys.exit(1)
                
                print(f"[effects] LUFS-I EN VOX: {lufs_en:.1f} dB")
                print(f"[effects] LUFS-I ES VOX: {lufs_es:.1f} dB")
                
                delta = lufs_en - lufs_es
                
                print(f"[effects] LUFS Delta: {delta:+.1f} dB")
                print(f"[effects] Adding 'JS: Volume Adjustment' to ES VOX...")
                
                fx_index = RPR.TrackFX_AddByName(track_es_vox.id, "JS: Volume Adjustment", False, -1)
                
                if fx_index >= 0:
                    RPR.TrackFX_SetParam(track_es_vox.id, fx_index, 0, delta)
                    print(f"[effects] OK: Volume Adjustment added with gain {delta:+.1f} dB")
                else:
                    print(f"[effects] ERROR: Could not add Volume Adjustment")
            
            # === EQ ===
            if add_eq:
                print(f"[effects]")
                print(f"[effects] === EQUALIZATION ===")
                print(f"[effects] Adding ReaEQ to ES VOX...")
                
                fx_index = RPR.TrackFX_AddByName(track_es_vox.id, "VST: ReaEQ (Cockos)", False, -1)
                
                if fx_index >= 0:
                    # Load preset "Holmes_1.0.1"
                    load_reaeq_preset(track_es_vox, fx_index, config.REAEQ_PRESET)
                else:
                    print(f"[effects] ERROR: Could not add ReaEQ")
            
            # === REAFIR ===
            if add_reafir:
                print(f"[effects]")
                print(f"[effects] === NOISE REDUCTION ===")
                print(f"[effects] Adding ReaFir to ES VOX...")
                
                fx_index = RPR.TrackFX_AddByName(track_es_vox.id, "VST: ReaFir (Cockos)", False, -1)
                
                if fx_index >= 0:
                    print(f"[effects] OK: ReaFir added")
                    print(f"[effects] NOTE: Configure manually:")
                    print(f"[effects]   - Mode: Subtract")
                    print(f"[effects]   - Generate noise profile in silent section")
                else:
                    print(f"[effects] ERROR: Could not add ReaFir")
            
            # Deselect all
            RPR.Main_OnCommand(40297, 0)
            
            # Update
            RPR.UpdateArrange()
            
            # Save project
            RPR.Main_SaveProject(project.id, False)
            
            print(f"[effects]")
            print(f"[effects] OK: Effects applied successfully")
            
    except Exception as e:
        print(f"[effects] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Parse arguments
    en_vox_file = None
    es_vox_file = None
    add_volume = False
    add_eq = False
    add_reafir = False
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--en-vox" and i + 1 < len(sys.argv):
            en_vox_file = Path(sys.argv[i + 1])
            i += 2
        elif arg == "--es-vox" and i + 1 < len(sys.argv):
            es_vox_file = Path(sys.argv[i + 1])
            i += 2
        elif arg == "--volume":
            add_volume = True
            i += 1
        elif arg == "--eq":
            add_eq = True
            i += 1
        elif arg == "--reafir":
            add_reafir = True
            i += 1
        elif arg == "--all":
            add_volume = True
            add_eq = True
            add_reafir = True
            i += 1
        else:
            i += 1
    
    print(f"[effects] Sherlock Audio Pipeline - Effect Applicator")
    print(f"[effects]")
    print(f"[effects] Configuration:")
    print(f"[effects]   Volume Adjustment: {'YES' if add_volume else 'NO'}")
    print(f"[effects]   ReaEQ: {'YES' if add_eq else 'NO'}")
    print(f"[effects]   ReaFir: {'YES' if add_reafir else 'NO'}")
    print(f"[effects]")
    
    if not en_vox_file or not es_vox_file:
        print(f"[effects] Usage:")
        print(f"[effects]   python3 steps/reaper_effects.py --en-vox <file> --es-vox <file> [--volume] [--eq] [--reafir] [--all]")
        print(f"[effects]")
        sys.exit(1)
    
    print(f"[effects] Ensure Reaper is open with the project loaded")
    print(f"[effects]")
    
    apply_effects_to_project(en_vox_file, es_vox_file, add_volume, add_eq, add_reafir)
