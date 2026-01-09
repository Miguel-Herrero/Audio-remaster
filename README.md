# Sherlock Holmes Audio Remastering Pipeline

## Project Overview

This project provides an automated pipeline for remastering the audio of the 1984 Sherlock Holmes TV series. The workflow reconstructs Spanish audio by combining English vocals with Spanish music tracks, applies audio effects, and creates final MKV files with multiple audio tracks and subtitles.

### Main Objective

Transform the original Sherlock Holmes episodes (available in English and Spanish) into high-quality remasters with:
- **Spanish Audio (ES MIX)**: Reconstructed audio combining English vocals with Spanish music
- **English Audio (EN)**: Original English audio preserved as FLAC
- **Japanese Audio (JA)**: Original Japanese audio preserved as FLAC (if available)
- **Subtitles**: All original subtitles plus optional 4K subtitles
- **4K Video Support**: Optional remux with 4K video source

## Project Structure

```
Audio-remaster/
├── README.md                    # This file
├── steps/                       # Processing scripts (pipeline steps)
│   ├── extract.py              # Step 1: Extract vocals and music from source files
│   ├── reaper_project.py       # Step 2: Create Reaper project with stems
│   ├── reaper_effects.py       # Step 3: Apply audio effects (EQ, volume, denoiser)
│   ├── reaper_render.py        # Step 4: Render final ES MIX audio
│   ├── mux_final.py            # Step 5: Create final MKV with all audio tracks
│   └── remux_4k.py             # Step 6 (optional): Create 4K version
├── workspace/                   # Working directory
│   ├── stems/                  # Extracted vocals and music (Step 1)
│   ├── projects/               # Reaper project files (Step 2)
│   └── output/                 # Final rendered audio and MKV files (Steps 4-6)
├── venv/                       # Python virtual environment
└── requirements.txt            # Python dependencies

Source files location (external):
/Users/miguelherrerobaena/Movies/s01eXX/
├── 00_sources/                 # Original MKV files (EN, 4K sources)
└── 01_stems/                   # MVSEP stems (ES vocals/music)
```

## Workflow Overview

The pipeline consists of 6 main steps:

### Step 1: Extract Stems
**Script**: `steps/extract.py`

Extracts vocals and music from English and Spanish source files using FFmpeg.

**What it does**:
- Extracts EN vocals from English source (or from MVSEP if pre-separated)
- Extracts EN music from English source (or from MVSEP if pre-separated)
- Extracts ES vocals from MVSEP stems
- Extracts ES music from MVSEP stems
- Converts all to FLAC 48kHz 16-bit

**Usage**:
```bash
python3 steps/extract.py \
  --en-source "/path/to/en_source.mkv" \
  --es-vocals "/path/to/mvsep_es_vocals.flac" \
  --es-music "/path/to/mvsep_es_music.flac" \
  --output-dir "workspace/stems"
```

**Outputs**:
- `en_vocals.flac`
- `en_music.flac`
- `es_vocals.flac`
- `es_music.flac`

---

### Step 2: Create Reaper Project
**Script**: `steps/reaper_project.py`

Creates a Reaper DAW project with all stem tracks properly configured.

**What it does**:
- Creates a new Reaper project (.RPP file)
- Adds 4 tracks: EN VOX, ES VOX, EN MUSIC, ES MUSIC
- Imports stem files as media items
- Synchronizes all tracks at timecode 00:00:00.000
- Sets project sample rate to 48kHz

**Usage**:
```bash
python3 steps/reaper_project.py \
  --project-name "s01e06" \
  --en-vox "workspace/stems/en_vocals.flac" \
  --es-vox "workspace/stems/es_vocals.flac" \
  --en-music "workspace/stems/en_music.flac" \
  --es-music "workspace/stems/es_music.flac" \
  --output-dir "workspace/projects"
```

**Outputs**:
- `workspace/projects/s01e06.RPP` (Reaper project file)

**Note**: You must manually open the project in Reaper to proceed with the next steps.

---

### Step 3: Apply Audio Effects
**Script**: `steps/reaper_effects.py`

Automatically applies audio effects to the ES VOX track in an open Reaper project.

**What it does**:
- **Volume Adjustment** (optional): Normalizes ES VOX to match EN VOX using LUFS-I measurement
- **EQ** (optional): Applies "Holmes_1.0.1" preset to ES VOX using ReaEQ
- **Noise Reduction** (optional): Adds ReaFir denoiser (requires manual configuration)

**Usage**:
```bash
# Apply only EQ (fast)
python3 steps/reaper_effects.py \
  --en-vox "workspace/stems/en_vocals.flac" \
  --es-vox "workspace/stems/es_vocals.flac" \
  --eq

# Apply everything
python3 steps/reaper_effects.py \
  --en-vox "workspace/stems/en_vocals.flac" \
  --es-vox "workspace/stems/es_vocals.flac" \
  --all
```

**Requirements**:
- Reaper must be running with the project open
- `reapy_boost` library must be installed
- ReaEQ preset "Holmes_1.0.1" must exist in Reaper

**Options**:
- `--volume`: Add volume normalization based on LUFS delta
- `--eq`: Add ReaEQ with preset
- `--reafir`: Add ReaFir noise reduction (requires manual setup)
- `--all`: Apply all effects

---

### Step 4: Render ES MIX
**Script**: `steps/reaper_render.py`

Renders the final ES MIX audio (ES VOX + EN MUSIC) to FLAC.

**What it does**:
- Solos ES VOX and EN MUSIC tracks
- Mutes all other tracks
- Renders using Reaper's configured render settings
- Restores original track states after render

**Usage**:
```bash
python3 steps/reaper_render.py "workspace/output"
```

**Requirements**:
- Reaper must be running with the project open and saved
- Render settings must be configured in Reaper:
  - Source: Master mix
  - Bounds: Entire project
  - Sample rate: 48000 Hz
  - Format: FLAC, 16 bit
  - File name: `$project` (uses project name)

**Outputs**:
- `workspace/output/s01e06.flac` (final ES MIX audio)

---

### Step 5: Create Final MKV
**Script**: `steps/mux_final.py`

Creates the final MKV file with video, all audio tracks, and subtitles.

**What it does**:
- Extracts EN and JA audio from original MKV and converts to FLAC
- Combines video from original MKV with:
  - ES MIX audio (Spanish, default track)
  - EN audio (English, original flag)
  - JA audio (Japanese, if available)
  - All original subtitles
- Sets proper track flags and language codes

**Usage**:
```bash
python3 steps/mux_final.py \
  "/path/to/original.mkv" \
  "workspace/output/s01e06.flac" \
  "workspace/output/Sherlock Holmes.s01e06.The Speckled Band.mkv" \
  "Sherlock Holmes (1984) S01E06: The Speckled Band"
```

**Arguments**:
1. Original MKV file (source video, EN/JA audio, subtitles)
2. Rendered ES MIX audio (from Step 4)
3. Output MKV file path
4. Title (optional)

**Outputs**:
- Final MKV with multiple audio tracks and subtitles

**Track Order**:
1. Video (from original)
2. Audio ES (Spanish, default)
3. Audio EN (English, original)
4. Audio JA (Japanese, if available)
5. Subtitles (from original)

---

### Step 6: Create 4K Version (Optional)
**Script**: `steps/remux_4k.py`

Creates a 4K version by replacing video with a 4K source while keeping all audio tracks.

**What it does**:
- Extracts video from 4K source MKV
- Keeps all audio and subtitle tracks from base MKV (Step 5 output)
- Optionally adds external 4K subtitles
- Updates title to include "[4K]" tag

**Usage**:
```bash
# Without external subtitles
python3 steps/remux_4k.py \
  "workspace/output/Sherlock Holmes.s01e06.mkv" \
  "/path/to/4k_source.mkv" \
  "workspace/output/Sherlock Holmes.s01e06 [4K].mkv"

# With external subtitles
python3 steps/remux_4k.py \
  "workspace/output/Sherlock Holmes.s01e06.mkv" \
  "/path/to/4k_source.mkv" \
  "workspace/output/Sherlock Holmes.s01e06 [4K].mkv" \
  "/path/to/4k_subtitles.srt"
```

**Arguments**:
1. Base MKV (from Step 5, contains all audio tracks)
2. 4K video source MKV
3. Output 4K MKV file path
4. External subtitles (optional, SRT/ASS format)

**Outputs**:
- 4K MKV with all audio tracks and subtitles

---

## Requirements

### System Requirements
- macOS (or Linux with adjustments)
- Python 3.9+
- FFmpeg
- mkvtoolnix (mkvmerge, mkvextract)
- Reaper DAW (for steps 2-4)

### Python Dependencies
```
reapy-boost>=0.10.0
```

### Installation

1. **Install system tools**:
```bash
# macOS with Homebrew
brew install ffmpeg mkvtoolnix
```

2. **Install Reaper**:
Download from [reaper.fm](https://www.reaper.fm)

3. **Setup Python environment**:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

4. **Configure reapy_boost**:
```bash
# Enable reapy web interface in Reaper
python3 -c "import reapy_boost; reapy_boost.config.enable_dist_api()"
```

5. **Create ReaEQ preset "Holmes_1.0.1"** in Reaper:
   - Band 1: High-pass 80 Hz, BW 2.00 oct
   - Band 2: Band 180 Hz, -3 dB, Q 1.00
   - Band 3: Band 400 Hz, -2 dB, Q 1.20
   - Band 4: High-shelf 5000 Hz, -3 dB, Q 2.00

---

## Complete Workflow Example

```bash
# Activate virtual environment
source venv/bin/activate

# Step 1: Extract stems
python3 steps/extract.py \
  --en-source "/Users/miguelherrerobaena/Movies/s01e06/00_sources/Sherlock.Holmes.s01e06.mkv" \
  --es-vocals "/Users/miguelherrerobaena/Movies/s01e06/01_stems/es_vocals_mvsep.flac" \
  --es-music "/Users/miguelherrerobaena/Movies/s01e06/01_stems/es_music_mvsep.flac" \
  --output-dir "workspace/stems"

# Step 2: Create Reaper project
python3 steps/reaper_project.py \
  --project-name "s01e06" \
  --en-vox "workspace/stems/en_vocals.flac" \
  --es-vox "workspace/stems/es_vocals.flac" \
  --en-music "workspace/stems/en_music.flac" \
  --es-music "workspace/stems/es_music.flac" \
  --output-dir "workspace/projects"

# Manually open workspace/projects/s01e06.RPP in Reaper

# Step 3: Apply effects (with Reaper running and project open)
python3 steps/reaper_effects.py \
  --en-vox "workspace/stems/en_vocals.flac" \
  --es-vox "workspace/stems/es_vocals.flac" \
  --all

# Step 4: Render ES MIX (with Reaper running and project open)
python3 steps/reaper_render.py "workspace/output"

# Step 5: Create final MKV
python3 steps/mux_final.py \
  "/Users/miguelherrerobaena/Movies/s01e06/00_sources/Sherlock.Holmes.s01e06.mkv" \
  "workspace/output/s01e06.flac" \
  "workspace/output/Sherlock Holmes.s01e06.The Speckled Band.mkv" \
  "Sherlock Holmes (1984) S01E06: The Speckled Band"

# Step 6 (optional): Create 4K version
python3 steps/remux_4k.py \
  "workspace/output/Sherlock Holmes.s01e06.The Speckled Band.mkv" \
  "/Users/miguelherrerobaena/Movies/s01e06/00_sources/Sherlock.Holmes.s01e06.4K.mkv" \
  "workspace/output/Sherlock Holmes.s01e06.The Speckled Band [4K].mkv" \
  "/Users/miguelherrerobaena/Movies/s01e06/00_sources/4k_subtitles.srt"
```

---

## Technical Details

### Audio Processing
- **Sample Rate**: 48kHz (standard for video)
- **Bit Depth**: 16-bit (sufficient quality, smaller files)
- **Codec**: FLAC (lossless compression)
- **LUFS Normalization**: Matches ES VOX loudness to EN VOX using LUFS-I measurement
- **EQ Profile**: Custom "Holmes_1.0.1" preset designed for vocal clarity

### Video Handling
- Original video tracks are preserved without re-encoding
- 4K video can be swapped in Step 6 without affecting audio

### MKV Structure
- **Default audio**: Spanish (ES MIX)
- **Original flag**: English audio marked as original
- **Track order**: Video → ES → EN → JA → Subtitles

---

## Troubleshooting

### mkvmerge library errors
If you see "Library not loaded: libfmt" errors:
```bash
brew reinstall mkvtoolnix
```

### reapy_boost connection issues
1. Ensure Reaper is running
2. Enable web interface: `python3 -c "import reapy_boost; reapy_boost.config.enable_dist_api()"`
3. Restart Reaper

### LUFS calculation too slow
Use `--eq` only (skip `--volume` flag) for faster processing. Volume normalization is optional.

### ReaEQ preset not found
Create the "Holmes_1.0.1" preset manually in Reaper or skip EQ with no flags.

---

## Project Context

This pipeline was developed to restore and enhance the Spanish audio of the 1984 Sherlock Holmes TV series. The original Spanish dub has poor quality vocals, but the music track is excellent. By combining English vocals with Spanish music, we create a superior listening experience while preserving the original score.

The workflow is designed to be:
- **Automated**: Minimal manual intervention required
- **Reproducible**: Same results across episodes
- **Flexible**: Optional steps for different quality requirements
- **Professional**: Uses industry-standard tools (Reaper, FFmpeg, FLAC)

---

## License

This project is for personal use. Sherlock Holmes (1984) is owned by Granada Television.

---

## Author

Miguel Herrero Baena
- Backend software engineer specializing in audio processing pipelines
- Based in Madrid, Spain
