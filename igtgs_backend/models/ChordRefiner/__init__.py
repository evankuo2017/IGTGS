"""
ChordRefiner：四類 quality（maj / maj7 / min / min7）細化用 CNN。
權重預設路徑：同目錄下 best_chord_model.pth（可用環境變數 IGTGS_CHORD_REFINER_WEIGHTS 覆寫）。
"""
from __future__ import annotations

from .model import ChordRefinerCNN
from .predict import predict_audio, predict_single_window

__all__ = ["ChordRefinerCNN", "predict_single_window", "predict_audio"]
