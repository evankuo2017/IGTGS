# Beat This! Web Application

Audio beat and downbeat tracking using Beat This! model (ISMIR 2024).

## System Requirements

- Python 3.10+
- Conda

## Installation

```bash
# Step 1: Create conda environment
conda create -n beatthis python=3.10 -y
conda activate beatthis

# Step 2: Install system dependencies
conda install -c conda-forge ffmpeg git -y

# Step 3: Install PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Step 4: Install Beat This! dependencies
pip install tqdm einops soxr rotary-embedding-torch

# Step 5: Install application dependencies
pip install -r requirements.txt

# Step 6: Install Beat This! package
python setup.py
```

## Running

```bash
conda activate beatthis
cd ~/project/IGTGS
python beatthis_app.py
```

Open browser: http://localhost:5000

Stop server: `Ctrl+C`
