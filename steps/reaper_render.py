import sys
from pathlib import Path

try:
    import reapy_boost as rpr
    from reapy_boost import reascript_api as RPR
except ImportError:
    print("[render] ERROR: reapy_boost no instalado")
    sys.exit(1)


def render_mix(output_dir: Path):
    """
    Renderiza ES VOX + EN M&E a FLAC 48kHz 16-bit.
    El nombre del archivo lo genera Reaper usando $project (ej: s01e06.flac).
    """
    
    print(f"[render] Conectando con Reaper...")
    
    try:
        with rpr.inside_reaper():
            project = rpr.Project()
            
            project_name = project.name if hasattr(project, 'name') else 'Sin guardar'
            print(f"[render] Proyecto activo: {project_name}")
            
            # Buscar las pistas por nombre
            tracks = list(project.tracks)
            
            track_en_vox = None
            track_es_vox = None
            track_en_music = None
            track_es_music = None
            
            for track in tracks:
                if "EN VOX" in track.name:
                    track_en_vox = track
                elif "ES VOX" in track.name:
                    track_es_vox = track
                elif "EN M&E" in track.name:
                    track_en_music = track
                elif "ES M&E" in track.name:
                    track_es_music = track
            
            if not track_es_vox:
                print(f"[render] ERROR: No se encontró pista 'ES VOX'")
                sys.exit(1)
            
            if not track_en_music:
                print(f"[render] ERROR: No se encontró pista 'EN M&E'")
                sys.exit(1)
            
            print(f"[render] OK: Pistas encontradas")
            print(f"[render]   - {track_es_vox.name}")
            print(f"[render]   - {track_en_music.name}")
            
            # Guardar estado original de mute/solo
            print(f"[render] Guardando estado original de pistas...")
            original_states = {}
            for track in tracks:
                mute = RPR.GetMediaTrackInfo_Value(track.id, "B_MUTE")
                solo = RPR.GetMediaTrackInfo_Value(track.id, "I_SOLO")
                original_states[track.id] = {"mute": mute, "solo": solo}
            
            # Configurar: solo ES VOX y EN M&E
            print(f"[render] Configurando pistas para render...")
            print(f"[render]   Solo: ES VOX + EN M&E")
            print(f"[render]   Mute: Resto de pistas")
            
            for track in tracks:
                if track.id == track_es_vox.id or track.id == track_en_music.id:
                    # Activar estas pistas
                    RPR.SetMediaTrackInfo_Value(track.id, "B_MUTE", 0)
                    RPR.SetMediaTrackInfo_Value(track.id, "I_SOLO", 1)
                else:
                    # Silenciar el resto
                    RPR.SetMediaTrackInfo_Value(track.id, "B_MUTE", 1)
                    RPR.SetMediaTrackInfo_Value(track.id, "I_SOLO", 0)
            
            # Configurar render settings
            print(f"[render] Configurando render...")
            print(f"[render]   Directorio de salida: {output_dir}")
            print(f"[render]   Formato: FLAC 16-bit 48 KHz")
            print(f"[render]   Nombre archivo: Configurado en Reaper ($project)")
            
            # Configurar directorio de salida
            RPR.GetSetProjectInfo_String(project.id, "RENDER_FILE", str(output_dir), True)
            
            # Actualizar
            RPR.UpdateArrange()
            
            print(f"[render] Iniciando render...")
            print(f"[render] NOTA: El preset de render debe estar configurado para:")
            print(f"[render]   - Source: Master mix")
            print(f"[render]   - Bounds: Entire project")
            print(f"[render]   - Format: FLAC, 48000 Hz, 16 bit")
            print(f"[render]   - File name: $project (wildcard)")
            
            # Ejecutar render usando los últimos settings
            # Action 41824 = File: Render project, using the most recent render settings
            RPR.Main_OnCommand(41824, 0)
            
            # Esperar un momento para que el render se inicie
            import time
            time.sleep(2)
            
            print(f"[render] Render iniciado...")
            print(f"[render] Esperando a que termine el render...")
            
            # Restaurar estado original
            print(f"[render] Restaurando estado original de pistas...")
            for track in tracks:
                if track.id in original_states:
                    RPR.SetMediaTrackInfo_Value(track.id, "B_MUTE", original_states[track.id]["mute"])
                    RPR.SetMediaTrackInfo_Value(track.id, "I_SOLO", original_states[track.id]["solo"])
            
            # Actualizar
            RPR.UpdateArrange()
            
            # Calcular nombre final del archivo
            project_basename = Path(project_name).stem if project_name != 'Sin guardar' else 'output'
            final_file = output_dir / f"{project_basename}.flac"
            
            print(f"[render]")
            print(f"[render] OK: Render completado")
            print(f"[render] Archivo esperado: {final_file}")
            
    except Exception as e:
        print(f"[render] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"[render] Sherlock Audio Pipeline - Render Mix")
        print(f"[render]")
        print(f"[render] Renderiza ES VOX + EN M&E usando los render settings de Reaper.")
        print(f"[render] El nombre del archivo lo genera Reaper usando el wildcard $project.")
        print(f"[render]")
        print(f"[render] Uso:")
        print(f"[render]   python3 steps/reaper_render.py <directorio_salida>")
        print(f"[render]")
        print(f"[render] Ejemplo:")
        print(f"[render]   python3 steps/reaper_render.py '/Users/miguelherrerobaena/Developer/github.com/Miguel-Herrero/Audio-remaster/workspace/output'")
        print(f"[render]")
        print(f"[render] Si el proyecto se llama 's01e06.RPP', el archivo final será:")
        print(f"[render]   /Users/miguelherrerobaena/Developer/github.com/Miguel-Herrero/Audio-remaster/workspace/output/s01e06.flac")
        print(f"[render]")
        print(f"[render] CONFIGURACION REQUERIDA EN REAPER:")
        print(f"[render]   1. File > Render...")
        print(f"[render]   2. Source: Master mix")
        print(f"[render]   3. Bounds: Entire project")
        print(f"[render]   4. Sample rate: 48000 Hz")
        print(f"[render]   5. Format: FLAC, 16 bit")
        print(f"[render]   6. Output: Directory + $project")
        print(f"[render]   7. Guardar como preset (opcional)")
        sys.exit(1)
    
    output_dir = Path(sys.argv[1])
    
    # Crear directorio si no existe
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"[render] Sherlock Audio Pipeline - Render Mix")
    print(f"[render]")
    print(f"[render] Asegúrate de que Reaper está abierto con el proyecto cargado")
    print(f"[render]")
    
    render_mix(output_dir)
