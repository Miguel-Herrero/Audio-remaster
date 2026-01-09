# enable_reapy.py
# Ejecutar desde Actions -> Load ReaScript dentro de Reaper

import sys

# Anadir el Python del sistema donde instalamos reapy-boost
sys.path.insert(0, '/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9/site-packages')

try:
    import reapy_boost
    reapy_boost.config.enable_dist_api()
    
    # Mostrar mensaje de exito en consola de Reaper
    try:
        from reaper_python import RPR_ShowConsoleMsg
        RPR_ShowConsoleMsg("OK: reapy_boost API habilitada correctamente\n")
        RPR_ShowConsoleMsg("Ahora puedes controlar Reaper desde Python externo\n")
    except:
        print("OK: reapy_boost API habilitada")
        
except Exception as e:
    try:
        from reaper_python import RPR_ShowConsoleMsg
        RPR_ShowConsoleMsg("ERROR: " + str(e) + "\n")
    except:
        print("ERROR: " + str(e))
