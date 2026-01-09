import sys
try:
    import reapy_boost as rpr
    from reapy_boost import reascript_api as RPR
except ImportError:
    print("ERROR: reapy_boost no instalado")
    sys.exit(1)

with rpr.inside_reaper():
    project = rpr.Project()
    tracks = list(project.tracks)
    
    track_es_vox = None
    for track in tracks:
        if "ES VOX" in track.name:
            track_es_vox = track
            break
    
    if not track_es_vox:
        print("ERROR: No se encontró pista 'ES VOX'")
        sys.exit(1)
    
    print(f"Buscando ReaEQ en pista '{track_es_vox.name}'...")
    
    # Buscar ReaEQ
    fx_index = -1
    for i, fx in enumerate(track_es_vox.fxs):
        print(f"  FX {i}: {fx.name}")
        if "ReaEQ" in fx.name:
            fx_index = i
            break
    
    if fx_index < 0:
        print("\nERROR: ReaEQ no está en la cadena de FX de ES VOX")
        sys.exit(1)
    
    print(f"\nReaEQ encontrado en índice {fx_index}")
    
    # Usar API de bajo nivel para obtener parámetros
    num_params = RPR.TrackFX_GetNumParams(track_es_vox.id, fx_index)
    print(f"\nReaEQ tiene {num_params} parámetros:")
    print("-" * 90)
    
    for param_idx in range(num_params):
        # Get parameter name
        retval_name = RPR.TrackFX_GetParamName(track_es_vox.id, fx_index, param_idx, "", 512)
        param_name = retval_name[4] if len(retval_name) > 4 else "Unknown"
        
        # Get parameter value
        result_val = RPR.TrackFX_GetParam(track_es_vox.id, fx_index, param_idx, 0.0, 0.0)
        value_raw = result_val[1] if len(result_val) > 1 else 0.0
        
        # Convertir a float si es string
        try:
            value = float(value_raw)
        except:
            value = 0.0
        
        # Get formatted value
        retval_fmt = RPR.TrackFX_GetFormattedParamValue(track_es_vox.id, fx_index, param_idx, "", 512)
        formatted_value = retval_fmt[4] if len(retval_fmt) > 4 else ""
        
        print(f"[{param_idx:3d}] {param_name:45s} = {value:.6f} ({formatted_value})")
