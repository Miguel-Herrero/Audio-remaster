import subprocess
import sys
import time
import requests
from pathlib import Path

try:
    from . import config
except ImportError:
    import config

def separate_stems_mvsep_api(input_flac: Path, output_dir: Path, api_token: str = None, stem_type: str = "vocals") -> tuple[Path, Path]:
    """
    Separates audio into VOX and instrumental using MVSEP API.
    """
    if not api_token:
        print("[stems] ERROR: MVSEP API token is required")
        sys.exit(1)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"[stems] Uploading {input_flac.name} to MVSEP...")
    
    try:
        with open(input_flac, 'rb') as f:
            files = {'audiofile': f}
            data = {
                'api_token': api_token,
                'sep_type': config.MVSEP_SEP_TYPE,
                'add_opt1': config.MVSEP_ADD_OPT1,
                'output_format': config.MVSEP_OUTPUT_FORMAT,
                'is_demo': '0'
            }
            
            response = requests.post(config.MVSEP_API_URL, files=files, data=data)
            result = response.json()
            
            if not result.get('success'):
                error_msg = result.get('message', result.get('error', 'Unknown error'))
                print(f"[stems] ERROR: {error_msg}")
                sys.exit(1)
            
            separation_hash = result['data']['hash']
            print(f"[stems] ✓ Separation created: {separation_hash}")
    
    except Exception as e:
        print(f"[stems] ERROR: {e}")
        sys.exit(1)
    
    # Polling until finished
    print(f"[stems] Waiting for processing to complete...")
    max_attempts = 120
    attempt = 0
    
    while attempt < max_attempts:
        try:
            check_response = requests.get(
                config.MVSEP_CHECK_URL,
                params={'hash': separation_hash, 'api_token': api_token}
            )
            check_result = check_response.json()
            
            if not check_result.get('success'):
                print(f"[stems] ERROR: {check_result}")
                sys.exit(1)
            
            # Status is in the root, not in data
            status = check_result.get('status', 'unknown')
            
            if status == 'done':
                print(f"[stems] ✓ Separation completed")
                
                # Files are an array of objects with type and url
                files_array = check_result['data'].get('files', [])
                
                if not files_array:
                    print(f"[stems] ERROR: No files found")
                    print(f"[stems] Response: {check_result}")
                    sys.exit(1)
                
                # Look for vocals and other/instrumental in the array
                vocals_url = None
                instrumental_url = None
                
                for file_obj in files_array:
                    file_type = file_obj.get('type', '').lower()
                    if 'vocal' in file_type:
                        vocals_url = file_obj['url']
                    elif 'other' in file_type or 'instrumental' in file_type:
                        instrumental_url = file_obj['url']
                
                if not vocals_url or not instrumental_url:
                    print(f"[stems] ERROR: Vocals or instrumental not found")
                    print(f"[stems] Available files: {files_array}")
                    sys.exit(1)
                
                vocals_path = output_dir / f"{input_flac.stem}_vocals.flac"
                instrumental_path = output_dir / f"{input_flac.stem}_instrumental.flac"
                
                print(f"[stems] Downloading vocals from {vocals_url}...")
                vocals_data = requests.get(vocals_url).content
                vocals_path.write_bytes(vocals_data)
                print(f"[stems] ✓ Saved: {vocals_path}")
                
                print(f"[stems] Downloading instrumental from {instrumental_url}...")
                inst_data = requests.get(instrumental_url).content
                instrumental_path.write_bytes(inst_data)
                print(f"[stems] ✓ Saved: {instrumental_path}")
                
                return vocals_path, instrumental_path
                
            elif status == 'failed' or status == 'error':
                print(f"[stems] ERROR: Separation failed")
                print(f"[stems] Details: {check_result}")
                sys.exit(1)
            
            print(f"[stems] Status: {status}, waiting... ({attempt + 1}/{max_attempts})")
            time.sleep(5)
            attempt += 1
            
        except KeyboardInterrupt:
            print(f"\n[stems] Cancelled by user")
            sys.exit(1)
    
    print(f"[stems] ERROR: Timeout")
    sys.exit(1)
    
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 stems.py <input.flac> <output_dir> [api_token]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    token = sys.argv[3] if len(sys.argv) > 3 else None
    
    separate_stems_mvsep_api(input_path, output_path, token)
