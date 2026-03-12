"""
Path constants and utilities for the ChordMini application.

This module centralizes all path-related constants and provides utilities
for path resolution and validation.
"""

import os
import sys
from pathlib import Path
from utils.logging import log_info, log_debug, is_debug_enabled


# Base directories
BACKEND_DIR = Path(__file__).parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Model directories
CHORD_CNN_LSTM_DIR = BACKEND_DIR / "models" / "Chord-CNN-LSTM"

# Audio directory
AUDIO_DIR = PROJECT_ROOT / "public" / "audio"

# Template directory
TEMPLATES_DIR = BACKEND_DIR / "templates"


def setup_model_paths():
    """
    Add model directories to Python path for imports.

    This function should be called during application initialization
    to ensure model modules can be imported.
    """
    model_dirs = [
        str(CHORD_CNN_LSTM_DIR),
    ]

    for model_dir in model_dirs:
        if model_dir not in sys.path:
            sys.path.insert(0, model_dir)
            log_debug(f"Added {model_dir} to Python path")


def get_model_checkpoint_path(model_name: str) -> Path:
    """
    Get the checkpoint path for a specific model.

    Args:
        model_name: Name of the model ('chord-cnn-lstm')

    Returns:
        Path: Path to the model checkpoint
    """
    if model_name == 'chord-cnn-lstm':
        return CHORD_CNN_LSTM_DIR  # Directory contains the model
    else:
        raise ValueError(f"Unknown model: {model_name}")


def get_model_config_path(model_name: str) -> Path:
    """
    Get the config path for a specific model.
    """
    raise ValueError(f"Model {model_name} does not have a config file")


def ensure_directories_exist():
    """
    Ensure that required directories exist.

    Creates directories if they don't exist.
    """
    directories = [
        AUDIO_DIR,
        TEMPLATES_DIR
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        log_debug(f"Ensured directory exists: {directory}")


def get_audio_file_path(filename: str) -> Path:
    """
    Get the full path to an audio file in the audio directory.

    Args:
        filename: Name of the audio file

    Returns:
        Path: Full path to the audio file
    """
    return AUDIO_DIR / filename


def validate_model_paths() -> dict:
    """
    Validate that model paths exist and are accessible.

    Returns:
        dict: Validation results for each model
    """
    results = {}

    # Check Chord CNN LSTM
    results['chord_cnn_lstm'] = {
        'dir_exists': CHORD_CNN_LSTM_DIR.exists(),
        'dir_path': str(CHORD_CNN_LSTM_DIR),
        'required_files': ['chord_recognition.py']
    }

    # Check audio directory
    results['audio_dir'] = {
        'exists': AUDIO_DIR.exists(),
        'path': str(AUDIO_DIR)
    }

    return results


# Initialize paths on import (debug-only)
if is_debug_enabled():
    log_debug(f"Audio directory path: {AUDIO_DIR}")
    log_debug(f"Chord CNN LSTM directory: {CHORD_CNN_LSTM_DIR}")