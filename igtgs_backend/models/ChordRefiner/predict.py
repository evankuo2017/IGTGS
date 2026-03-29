from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import torch
import torch.nn.functional as F
from tqdm import tqdm

from . import config
from .model import ChordRefinerCNN


def predict_single_window(
    y: np.ndarray,
    sr: int,
    model: torch.nn.Module,
    device: torch.device,
    window_sec: float = 2.0,
) -> tuple[str, float, dict[str, float]]:
    """
    單視窗推理：將波形補滿至 window_sec（不足則 zero-pad），回傳 argmax label、該類別 softmax 信心值、各類別機率。
    """
    if sr != config.SR:
        y = librosa.resample(y.astype(np.float32), orig_sr=sr, target_sr=config.SR)
        sr = config.SR
    window_samples = int(window_sec * sr)
    if len(y) > window_samples:
        y = y[:window_samples]
    if len(y) < window_samples:
        y = np.pad(y, (0, window_samples - len(y)), mode="constant")
    model.eval()
    with torch.no_grad():
        cqt = librosa.cqt(
            y,
            sr=sr,
            hop_length=config.HOP_LENGTH,
            n_bins=config.N_BINS,
            bins_per_octave=config.BINS_PER_OCTAVE,
        )
        cqt_db = librosa.amplitude_to_db(np.abs(cqt), ref=np.max)
        tensor = torch.tensor(cqt_db, dtype=torch.float32).unsqueeze(0).unsqueeze(0).to(device)
        logits = model(tensor)
        probs = F.softmax(logits, dim=1)[0].cpu().numpy()
    pred_idx = int(np.argmax(probs))
    label = config.IDX_2_LABEL[pred_idx]
    confidence = float(probs[pred_idx])
    prob_map = {config.IDX_2_LABEL[i]: float(probs[i]) for i in range(len(config.CHORD_LIST))}
    return label, confidence, prob_map


def predict_audio(
    wav_path: str,
    model: torch.nn.Module,
    device: torch.device,
    window_sec: float = 2.0,
    hop_sec: float = 0.5,
) -> tuple[str, list[dict]]:
    """Sliding window 對整段音檔推理（除錯／實驗用）。"""
    y, sr = librosa.load(wav_path, sr=config.SR)
    total_duration = len(y) / sr
    print(f"[Info] 音檔總長度: {total_duration:.2f} 秒")

    window_samples = int(window_sec * sr)
    hop_samples = int(hop_sec * sr)
    start_samples = list(range(0, max(1, len(y) - window_samples + 1), hop_samples))
    if len(y) < window_samples:
        start_samples = [0]

    predictions = []
    all_probs = []
    model.eval()
    with torch.no_grad():
        for start in tqdm(start_samples, desc="Sliding Window Inference"):
            end = start + window_samples
            y_segment = y[start:end]
            if len(y_segment) < window_samples:
                pad_length = window_samples - len(y_segment)
                y_segment = np.pad(y_segment, (0, pad_length), mode="constant")
            cqt = librosa.cqt(
                y_segment,
                sr=sr,
                hop_length=config.HOP_LENGTH,
                n_bins=config.N_BINS,
                bins_per_octave=config.BINS_PER_OCTAVE,
            )
            cqt_db = librosa.amplitude_to_db(np.abs(cqt), ref=np.max)
            cqt_tensor = torch.tensor(cqt_db, dtype=torch.float32).unsqueeze(0).unsqueeze(0).to(device)
            outputs = model(cqt_tensor)
            probs = F.softmax(outputs, dim=1)[0]
            pred_idx = torch.argmax(probs).item()
            pred_label = config.IDX_2_LABEL[pred_idx]
            all_probs.append(probs.cpu().numpy())
            predictions.append(
                {
                    "start": start / sr,
                    "end": (start + window_samples) / sr,
                    "label": pred_label,
                    "probs": probs.cpu().numpy(),
                }
            )

    avg_probs = np.mean(all_probs, axis=0)
    final_pred_idx = int(np.argmax(avg_probs))
    final_label = config.IDX_2_LABEL[final_pred_idx]
    print(f"\n最終判定: ** {final_label} **\n")
    return final_label, predictions


if __name__ == "__main__":
    _pkg = Path(__file__).resolve().parent
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    m = ChordRefinerCNN(num_classes=len(config.CHORD_LIST)).to(device)
    weights = _pkg / "best_chord_model.pth"
    if weights.is_file():
        try:
            m.load_state_dict(torch.load(str(weights), map_location=device, weights_only=True))
        except TypeError:
            m.load_state_dict(torch.load(str(weights), map_location=device))
        print(f"[Info] 已載入權重: {weights}")
    else:
        raise SystemExit(f"請將權重置於: {weights}")
