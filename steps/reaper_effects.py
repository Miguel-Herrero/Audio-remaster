import sys
import subprocess
import re
from pathlib import Path

try:
    import reapy_boost as rpr
    from reapy_boost import reascript_api as RPR
except ImportError:
    print("[effects] ERROR: reapy_boost no instalado")
    sys.exit(1)


def calculate_lufs_ffmpeg(audio_file: Path) -> float:
    """
    Calcula LUFS-I de un archivo de audio usando ffmpeg.
    """
    print(f"[effects] Calculando LUFS de {audio_file.name}...")
    
    cmd = [
        "ffmpeg",
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
    Carga un preset de ReaEQ por nombre.
    """
    print(f"[effects] Cargando preset '{preset_name}' en ReaEQ...")
    
    # TrackFX_SetPreset carga un preset por nombre
    success = RPR.TrackFX_SetPreset(track.id, fx_index, preset_name)
    
    if success:
        print(f"[effects] OK: Preset '{preset_name}' cargado")
    else:
        print(f"[effects] ERROR: No se pudo cargar preset '{preset_name}'")
        print(f"[effects] Verifica que el preset existe en Reaper")
    
    return success


def apply_effects_to_project(en_vox_file: Path = None, es_vox_file: Path = None, add_volume: bool = False, add_eq: bool = False, add_reafir: bool = False):
    """
    Aplica efectos al proyecto actualmente abierto en Reaper.
    """
    
    print(f"[effects] Conectando con Reaper...")
    
    try:
        with rpr.inside_reaper():
            project = rpr.Project()
            
            print(f"[effects] Proyecto activo: {project.name if hasattr(project, 'name') else 'Sin guardar'}")
            
            # Buscar las pistas por nombre
            tracks = list(project.tracks)
            
            track_en_vox = None
            track_es_vox = None
            
            for track in tracks:
                if "EN VOX" in track.name:
                    track_en_vox = track
                elif "ES VOX" in track.name:
                    track_es_vox = track
            
            if not track_en_vox:
                print(f"[effects] ERROR: No se encontro pista 'EN VOX'")
                sys.exit(1)
            
            if not track_es_vox:
                print(f"[effects] ERROR: No se encontro pista 'ES VOX'")
                sys.exit(1)
            
            print(f"[effects] OK: Pistas encontradas")
            print(f"[effects]   - {track_en_vox.name}")
            print(f"[effects]   - {track_es_vox.name}")
            
            # === VOLUME ADJUSTMENT ===
            if add_volume and en_vox_file and es_vox_file:
                print(f"[effects]")
                print(f"[effects] === NORMALIZACION LUFS ===")
                
                lufs_en = calculate_lufs_ffmpeg(en_vox_file)
                lufs_es = calculate_lufs_ffmpeg(es_vox_file)
                
                if not lufs_en:
                    print(f"[effects] ERROR: No se pudo calcular LUFS de EN VOX")
                    sys.exit(1)
                
                if not lufs_es:
                    print(f"[effects] ERROR: No se pudo calcular LUFS de ES VOX")
                    sys.exit(1)
                
                print(f"[effects] LUFS-I EN VOX: {lufs_en:.1f} dB")
                print(f"[effects] LUFS-I ES VOX: {lufs_es:.1f} dB")
                
                delta = lufs_en - lufs_es
                
                print(f"[effects] Delta LUFS: {delta:+.1f} dB")
                print(f"[effects] Añadiendo 'JS: Volume Adjustment' a ES VOX...")
                
                fx_index = RPR.TrackFX_AddByName(track_es_vox.id, "JS: Volume Adjustment", False, -1)
                
                if fx_index >= 0:
                    RPR.TrackFX_SetParam(track_es_vox.id, fx_index, 0, delta)
                    print(f"[effects] OK: Volume Adjustment añadido con ganancia {delta:+.1f} dB")
                else:
                    print(f"[effects] ERROR: No se pudo añadir Volume Adjustment")
            
            # === EQ ===
            if add_eq:
                print(f"[effects]")
                print(f"[effects] === ECUALIZACION ===")
                print(f"[effects] Añadiendo ReaEQ a ES VOX...")
                
                fx_index = RPR.TrackFX_AddByName(track_es_vox.id, "VST: ReaEQ (Cockos)", False, -1)
                
                if fx_index >= 0:
                    # Cargar preset "Holmes_1.0.1"
                    load_reaeq_preset(track_es_vox, fx_index, "Holmes_1.0.1")
                else:
                    print(f"[effects] ERROR: No se pudo añadir ReaEQ")
            
            # === REAFIR ===
            if add_reafir:
                print(f"[effects]")
                print(f"[effects] === REDUCCION DE RUIDO ===")
                print(f"[effects] Añadiendo ReaFir a ES VOX...")
                
                fx_index = RPR.TrackFX_AddByName(track_es_vox.id, "VST: ReaFir (Cockos)", False, -1)
                
                if fx_index >= 0:
                    print(f"[effects] OK: ReaFir añadido")
                    print(f"[effects] NOTA: Configura manualmente:")
                    print(f"[effects]   - Mode: Subtract")
                    print(f"[effects]   - Genera perfil de ruido en seccion silenciosa")
                else:
                    print(f"[effects] ERROR: No se pudo añadir ReaFir")
            
            # Deseleccionar todo
            RPR.Main_OnCommand(40297, 0)
            
            # Actualizar
            RPR.UpdateArrange()
            
            # Guardar proyecto
            RPR.Main_SaveProject(project.id, False)
            
            print(f"[effects]")
            print(f"[effects] OK: Efectos aplicados correctamente")
            
    except Exception as e:
        print(f"[effects] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Parsear argumentos
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
    
    print(f"[effects] Sherlock Audio Pipeline - Aplicador de Efectos")
    print(f"[effects]")
    print(f"[effects] Configuracion:")
    print(f"[effects]   Volume Adjustment: {'SI' if add_volume else 'NO'}")
    print(f"[effects]   ReaEQ: {'SI' if add_eq else 'NO'}")
    print(f"[effects]   ReaFir: {'SI' if add_reafir else 'NO'}")
    print(f"[effects]")
    
    if not en_vox_file or not es_vox_file:
        print(f"[effects] Uso:")
        print(f"[effects]   python3 steps/reaper_effects.py --en-vox <archivo> --es-vox <archivo> [--volume] [--eq] [--reafir] [--all]")
        print(f"[effects]")
        sys.exit(1)
    
    print(f"[effects] Asegurate de que Reaper esta abierto con el proyecto cargado")
    print(f"[effects]")
    
    apply_effects_to_project(en_vox_file, es_vox_file, add_volume, add_eq, add_reafir)
