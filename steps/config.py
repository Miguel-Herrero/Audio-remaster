import os
from pathlib import Path

# --- SYSTEM TOOLS ---
FFMPEG_PATH = "ffmpeg"
MKVMERGE_PATH = "/opt/homebrew/bin/mkvmerge"
MKVEXTRACT_PATH = "/opt/homebrew/bin/mkvextract"

# --- AUDIO SETTINGS ---
SAMPLE_RATE = 48000
BIT_DEPTH = 16
CODEC = "flac"
COMPRESSION_LEVEL = "8"

# --- REAPER TRACK NAMES ---
TRACK_VIDEO = "VIDEO (Reference)"
TRACK_EN_VOX = "EN VOX"
TRACK_EN_MUSIC = "EN M&E"
TRACK_ES_VOX = "ES VOX"
TRACK_ES_MUSIC = "ES M&E"

# --- REAPER EFFECTS ---
REAEQ_PRESET = "Holmes_1.0.1"

# --- MVSEP API SETTINGS ---
MVSEP_API_URL = "https://mvsep.com/api/separation/create"
MVSEP_CHECK_URL = "https://mvsep.com/api/separation/get"
MVSEP_SEP_TYPE = "40"  # BS Roformer vocals/instrumental
MVSEP_ADD_OPT1 = "81"  # Vocal model type: ver 2025.07
MVSEP_OUTPUT_FORMAT = "2"  # FLAC 16-bit lossless

# --- LANGUAGE CODES ---
LANG_ES = "spa"
LANG_EN = "eng"
LANG_JA = "jpn"

# --- DEFAULT DIRECTORIES ---
WORKSPACE_DIR = Path("workspace")
STEMS_DIR = WORKSPACE_DIR / "stems"
PROJECTS_DIR = WORKSPACE_DIR / "projects"
OUTPUT_DIR = WORKSPACE_DIR / "output"
