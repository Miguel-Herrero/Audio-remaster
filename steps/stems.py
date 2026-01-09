import subprocess
import sys
import time
import requests
from pathlib import Path

def separate_stems_mvsep_api(input_flac: Path, output_dir: Path, api_token: str = None, stem_type: str = "vocals") -> tuple[Path, Path]:
    """
    Separa audio en VOX e instrumental usando MVSEP API.
    """
    if not api_token:
        print("[stems] ERROR: Necesitas un API token de MVSEP")
        sys.exit(1)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"[stems] Subiendo {input_flac.name} a MVSEP...")
    
    try:
        with open(input_flac, 'rb') as f:
            files = {'audiofile': f}
            data = {
                'api_token': api_token,
                'sep_type': '40',  # BS Roformer vocals/instrumental
                'add_opt1': '81', # Vocal model type: ver 2025.07 (SDR vocals: 11.89, SDR instrum: 18.20)
                'output_format': '2',  # FLAC 16-bit lossless
                # 'output_format': '5',  # FLAC 24-bit lossless (Premium)
                'is_demo': '0'
            }
            
            response = requests.post('https://mvsep.com/api/separation/create', files=files, data=data)
            result = response.json()
            
            if not result.get('success'):
                error_msg = result.get('message', result.get('error', 'Unknown error'))
                print(f"[stems] ERROR: {error_msg}")
                sys.exit(1)
            
            separation_hash = result['data']['hash']
            print(f"[stems] ✓ Separación creada: {separation_hash}")
    
    except Exception as e:
        print(f"[stems] ERROR: {e}")
        sys.exit(1)
    
    # Polling hasta que termine
    print(f"[stems] Esperando a que termine el procesamiento...")
    max_attempts = 120
    attempt = 0
    
    while attempt < max_attempts:
        try:
            check_response = requests.get(
                'https://mvsep.com/api/separation/get',
                params={'hash': separation_hash, 'api_token': api_token}
            )
            check_result = check_response.json()
            
            if not check_result.get('success'):
                print(f"[stems] ERROR: {check_result}")
                sys.exit(1)
            
            # El status está en el root, no en data
            status = check_result.get('status', 'unknown')
            
            if status == 'done':
                print(f"[stems] ✓ Separación completada")
                
                # Los files son un array de objetos con type y url
                files_array = check_result['data'].get('files', [])
                
                if not files_array:
                    print(f"[stems] ERROR: No se encontraron archivos")
                    print(f"[stems] Respuesta: {check_result}")
                    sys.exit(1)
                
                # Buscar vocals y other/instrumental en el array
                vocals_url = None
                instrumental_url = None
                
                for file_obj in files_array:
                    file_type = file_obj.get('type', '').lower()
                    if 'vocal' in file_type:
                        vocals_url = file_obj['url']
                    elif 'other' in file_type or 'instrumental' in file_type:
                        instrumental_url = file_obj['url']
                
                if not vocals_url or not instrumental_url:
                    print(f"[stems] ERROR: No se encontraron vocals o instrumental")
                    print(f"[stems] Files disponibles: {files_array}")
                    sys.exit(1)
                
                vocals_path = output_dir / f"{input_flac.stem}_vocals.flac"
                instrumental_path = output_dir / f"{input_flac.stem}_instrumental.flac"
                
                print(f"[stems] Descargando vocals desde {vocals_url}...")
                vocals_data = requests.get(vocals_url).content
                vocals_path.write_bytes(vocals_data)
                print(f"[stems] ✓ Guardado: {vocals_path}")
                
                print(f"[stems] Descargando instrumental desde {instrumental_url}...")
                inst_data = requests.get(instrumental_url).content
                instrumental_path.write_bytes(inst_data)
                print(f"[stems] ✓ Guardado: {instrumental_path}")
                
                return vocals_path, instrumental_path
                
            elif status == 'failed' or status == 'error':
                print(f"[stems] ERROR: Separación falló")
                print(f"[stems] Detalles: {check_result}")
                sys.exit(1)
            
            print(f"[stems] Estado: {status}, esperando... ({attempt + 1}/{max_attempts})")
            time.sleep(5)
            attempt += 1
            
        except KeyboardInterrupt:
            print(f"\n[stems] Cancelado por usuario")
            sys.exit(1)
    
    print(f"[stems] ERROR: Timeout")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python3 stems.py <input.flac> <output_dir> [api_token]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    token = sys.argv[3] if len(sys.argv) > 3 else None
    
    separate_stems_mvsep_api(input_path, output_path, token)
