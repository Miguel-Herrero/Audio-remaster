import sys
import time
from pathlib import Path

try:
    import reapy_boost as rpr
    from reapy_boost import reascript_api as RPR
except ImportError:
    print("[reaper] ERROR: reapy_boost no instalado")
    print("[reaper] Instala con: pip3 install reapy-boost==0.10.201 typing_extensions")
    sys.exit(1)


def create_reaper_project(video_mkv: Path, en_vox: Path, en_me: Path, es_vox: Path, es_me: Path, output_dir: Path, project_name: str = "remaster") -> Path:
    """
    Crea proyecto de Reaper automáticamente con video y stems.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    project_path = output_dir / f"{project_name}.rpp"
    
    print(f"[reaper] Verificando archivos...")
    
    files_to_check = [
        ("Video MKV", video_mkv),
        ("EN VOX", en_vox),
        ("EN M&E", en_me),
        ("ES VOX", es_vox),
        ("ES M&E", es_me)
    ]
    
    for name, path in files_to_check:
        if not path.exists():
            print(f"[reaper] ERROR: No existe {name}: {path}")
            sys.exit(1)
    
    print(f"[reaper] OK: Todos los archivos encontrados")
    print(f"[reaper] Conectando con Reaper...")
    
    try:
        with rpr.inside_reaper():
            project = rpr.Project()
            
            print(f"[reaper] Creando pistas...")
            
            # Crear todas las pistas primero
            track_video = project.add_track(index=0, name="VIDEO (Reference)")
            track_video.master_send = False
            
            track_en_vox = project.add_track(index=1, name="EN VOX")
            track_en_me = project.add_track(index=2, name="EN M&E")
            track_es_vox = project.add_track(index=3, name="ES VOX")
            track_es_me = project.add_track(index=4, name="ES M&E")
            
            print(f"[reaper] OK: 5 pistas creadas")
            
            # Ahora añadir items a cada pista
            tracks_and_files = [
                (track_video, video_mkv, "Video"),
                (track_en_vox, en_vox, "EN VOX"),
                (track_en_me, en_me, "EN M&E"),
                (track_es_vox, es_vox, "ES VOX"),
                (track_es_me, es_me, "ES M&E")
            ]
            
            for track, file_path, name in tracks_and_files:
                print(f"[reaper] Importando {name}...")
                
                # Crear un item en la pista
                item = RPR.AddMediaItemToTrack(track.id)
                
                # Posicionar el item en 0
                RPR.SetMediaItemPosition(item, 0.0, False)
                
                # Añadir un take al item
                take = RPR.AddTakeToMediaItem(item)
                
                # Cargar el source
                source = RPR.PCM_Source_CreateFromFile(str(file_path.absolute()))
                
                if source:
                    # Asignarlo al take
                    RPR.SetMediaItemTake_Source(take, source)
                    
                    # Ajustar longitud del item basado en el source
                    # Usar GetMediaSourceLength con el parámetro correcto
                    length_info = RPR.GetMediaSourceLength(source, 0)
                    
                    # length_info puede ser un numero o una lista [length]
                    if isinstance(length_info, (list, tuple)):
                        length = length_info[0] if length_info else 100.0
                    else:
                        length = length_info if length_info else 100.0
                    
                    if length > 0:
                        RPR.SetMediaItemLength(item, length, False)
                    
                    # Actualizar el item
                    RPR.UpdateItemInProject(item)
                    
                    print(f"[reaper] OK: {name} importado (duracion: {length:.2f}s)")
                else:
                    print(f"[reaper] ERROR: No se pudo cargar {name}")
            
            # Actualizar el arrange view
            RPR.UpdateArrange()
            
            # Guardar proyecto
            RPR.Main_SaveProject(project.id, False)
            
            print(f"[reaper]")
            print(f"[reaper] OK: Proyecto creado exitosamente")
            print(f"[reaper]")
            print(f"[reaper] Estructura creada:")
            print(f"[reaper]   Track 1: VIDEO (sin master send)")
            print(f"[reaper]   Track 2: EN VOX")
            print(f"[reaper]   Track 3: EN M&E")
            print(f"[reaper]   Track 4: ES VOX")
            print(f"[reaper]   Track 5: ES M&E")
            print(f"[reaper]   Todos en posicion 0")
            print(f"[reaper]")
            print(f"[reaper] IMPORTANTE: Guarda el proyecto como:")
            print(f"[reaper]   {project_path}")
            print(f"[reaper]   (File > Save Project As...)")
            print(f"[reaper]")
            print(f"[reaper] PROXIMOS PASOS:")
            print(f"[reaper] 1. Guarda el proyecto")
            print(f"[reaper] 2. View > Video")
            print(f"[reaper] 3. Alinear ES VOX con EN VOX")
            print(f"[reaper] 4. Normalizar LUFS de ES VOX")
            print(f"[reaper] 5. Aplicar EQ y ReaFir a ES VOX")
            print(f"[reaper] 6. Render mezcla final")
            
            return project_path
            
    except Exception as e:
        print(f"[reaper] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Uso: python3 reaper_auto.py <video.mkv> <en_vox.flac> <en_me.flac> <es_vox.flac> <es_me.flac> <output_dir> [project_name]")
        sys.exit(1)
    
    video_path = Path(sys.argv[1])
    en_vox_path = Path(sys.argv[2])
    en_me_path = Path(sys.argv[3])
    es_vox_path = Path(sys.argv[4])
    es_me_path = Path(sys.argv[5])
    output_path = Path(sys.argv[6])
    proj_name = sys.argv[7] if len(sys.argv) > 7 else "remaster"
    
    create_reaper_project(video_path, en_vox_path, en_me_path, es_vox_path, es_me_path, output_path, proj_name)
