import sys
import time
from pathlib import Path

try:
    from . import config
except ImportError:
    import config

try:
    import reapy_boost as rpr
    from reapy_boost import reascript_api as RPR
except ImportError:
    print("[reaper] ERROR: reapy_boost not installed")
    print("[reaper] Install with: pip3 install reapy-boost==0.10.201 typing_extensions")
    sys.exit(1)


def create_reaper_project(video_mkv: Path, en_vox: Path, en_me: Path, es_vox: Path, es_me: Path, output_dir: Path, project_name: str = "remaster") -> Path:
    """
    Creates a Reaper project automatically with video and stems.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    project_path = output_dir / f"{project_name}.rpp"
    
    print(f"[reaper] Verifying files...")
    
    files_to_check = [
        ("Video MKV", video_mkv),
        (config.TRACK_EN_VOX, en_vox),
        (config.TRACK_EN_MUSIC, en_me),
        (config.TRACK_ES_VOX, es_vox),
        (config.TRACK_ES_MUSIC, es_me)
    ]
    
    for name, path in files_to_check:
        if not path.exists():
            print(f"[reaper] ERROR: {name} does not exist: {path}")
            sys.exit(1)
    
    print(f"[reaper] OK: All files found")
    print(f"[reaper] Connecting to Reaper...")
    
    try:
        with rpr.inside_reaper():
            project = rpr.Project()
            
            print(f"[reaper] Creating tracks...")
            
            # Create all tracks first
            track_video = project.add_track(index=0, name=config.TRACK_VIDEO)
            track_video.master_send = False
            
            track_en_vox = project.add_track(index=1, name=config.TRACK_EN_VOX)
            track_en_me = project.add_track(index=2, name=config.TRACK_EN_MUSIC)
            track_es_vox = project.add_track(index=3, name=config.TRACK_ES_VOX)
            track_es_me = project.add_track(index=4, name=config.TRACK_ES_MUSIC)
            
            print(f"[reaper] OK: 5 tracks created")
            
            # Now add items to each track
            tracks_and_files = [
                (track_video, video_mkv, config.TRACK_VIDEO),
                (track_en_vox, en_vox, config.TRACK_EN_VOX),
                (track_en_me, en_me, config.TRACK_EN_MUSIC),
                (track_es_vox, es_vox, config.TRACK_ES_VOX),
                (track_es_me, es_me, config.TRACK_ES_MUSIC)
            ]
            
            for track, file_path, name in tracks_and_files:
                print(f"[reaper] Importing {name}...")
                
                # Create a media item on the track
                item = RPR.AddMediaItemToTrack(track.id)
                
                # Position item at 0
                RPR.SetMediaItemPosition(item, 0.0, False)
                
                # Add a take to the item
                take = RPR.AddTakeToMediaItem(item)
                
                # Load the source
                source = RPR.PCM_Source_CreateFromFile(str(file_path.absolute()))
                
                if source:
                    # Assign to take
                    RPR.SetMediaItemTake_Source(take, source)
                    
                    # Adjust item length based on source
                    length_info = RPR.GetMediaSourceLength(source, 0)
                    
                    # length_info can be a number or a list [length]
                    if isinstance(length_info, (list, tuple)):
                        length = length_info[0] if length_info else 100.0
                    else:
                        length = length_info if length_info else 100.0
                    
                    if length > 0:
                        RPR.SetMediaItemLength(item, length, False)
                    
                    # Update item
                    RPR.UpdateItemInProject(item)
                    
                    print(f"[reaper] OK: {name} imported (duration: {length:.2f}s)")
                else:
                    print(f"[reaper] ERROR: Could not load {name}")
            
            # Update arrange view
            RPR.UpdateArrange()
            
            # Save project
            RPR.Main_SaveProject(project.id, False)
            
            print(f"[reaper]")
            print(f"[reaper] OK: Project created successfully")
            print(f"[reaper]")
            print(f"[reaper] Structure created:")
            print(f"[reaper]   Track 1: {config.TRACK_VIDEO} (no master send)")
            print(f"[reaper]   Track 2: {config.TRACK_EN_VOX}")
            print(f"[reaper]   Track 3: {config.TRACK_EN_MUSIC}")
            print(f"[reaper]   Track 4: {config.TRACK_ES_VOX}")
            print(f"[reaper]   Track 5: {config.TRACK_ES_MUSIC}")
            print(f"[reaper]   All aligned at 0")
            print(f"[reaper]")
            print(f"[reaper] IMPORTANT: Save the project as:")
            print(f"[reaper]   {project_path}")
            print(f"[reaper]   (File > Save Project As...)")
            print(f"[reaper]")
            print(f"[reaper] NEXT STEPS:")
            print(f"[reaper] 1. Save the project")
            print(f"[reaper] 2. View > Video")
            print(f"[reaper] 3. Align {config.TRACK_ES_VOX} with {config.TRACK_EN_VOX}")
            print(f"[reaper] 4. Normalize LUFS for {config.TRACK_ES_VOX}")
            print(f"[reaper] 5. Apply EQ and ReaFir to {config.TRACK_ES_VOX}")
            print(f"[reaper] 6. Render final mix")
            
            return project_path
            
    except Exception as e:
        print(f"[reaper] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: python3 reaper_project.py <video.mkv> <en_vox.flac> <en_me.flac> <es_vox.flac> <es_me.flac> <output_dir> [project_name]")
        sys.exit(1)
    
    video_path = Path(sys.argv[1])
    en_vox_path = Path(sys.argv[2])
    en_me_path = Path(sys.argv[3])
    es_vox_path = Path(sys.argv[4])
    es_me_path = Path(sys.argv[5])
    output_path = Path(sys.argv[6])
    proj_name = sys.argv[7] if len(sys.argv) > 7 else "remaster"
    
    create_reaper_project(video_path, en_vox_path, en_me_path, es_vox_path, es_me_path, output_path, proj_name)
