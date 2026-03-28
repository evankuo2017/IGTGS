import os
import torch
import torch.nn.functional as F
import librosa
import numpy as np
from tqdm import tqdm
import config
from model import ChordRefinerCNN


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


def predict_audio(wav_path, model, device, window_sec=2.0, hop_sec=0.5):
    """
    使用 Sliding Window 策略對任意長度的音檔進行和弦機率預測。
    """    
    # 1. 載入音檔
    y, sr = librosa.load(wav_path, sr=config.SR)
    total_duration = len(y) / sr
    print(f"[Info] 音檔總長度: {total_duration:.2f} 秒")

    window_samples = int(window_sec * sr)
    hop_samples = int(hop_sec * sr)

    predictions = []
    all_probs = []

    # 2. 計算滑動視窗的起始點
    start_samples = list(range(0, max(1, len(y) - window_samples + 1), hop_samples))
    
    # 如果音檔比我們設定的視窗（2秒）還要短，就只抓一次
    if len(y) < window_samples:
        start_samples = [0]

    model.eval()
    with torch.no_grad():
        for start in tqdm(start_samples, desc="Sliding Window Inference"):
            end = start + window_samples
            y_segment = y[start:end]

            # 如果是最後一個片段且長度不足 2 秒，我們在後面補零 (Padding) 讓它維持形狀
            if len(y_segment) < window_samples:
                pad_length = window_samples - len(y_segment)
                y_segment = np.pad(y_segment, (0, pad_length), mode='constant')

            # 計算 CQT 並轉為 dB 單位
            C = librosa.cqt(y_segment, sr=sr, hop_length=config.HOP_LENGTH, 
                            n_bins=config.N_BINS, bins_per_octave=config.BINS_PER_OCTAVE)
            C_db = librosa.amplitude_to_db(np.abs(C), ref=np.max)

            # 轉換為 PyTorch Tensor，形狀調整為 (Batch=1, Channel=1, H, W)
            cqt_tensor = torch.tensor(C_db, dtype=torch.float32).unsqueeze(0).unsqueeze(0).to(device)

            # 模型預測
            outputs = model(cqt_tensor)
            probs = F.softmax(outputs, dim=1)[0] # 取出 batch 0 的機率
            pred_idx = torch.argmax(probs).item()
            pred_label = config.IDX_2_LABEL[pred_idx]

            all_probs.append(probs.cpu().numpy())
            
            # 記錄這個時間段的預測結果
            start_time = start / sr
            end_time = (start + window_samples) / sr
            predictions.append({
                'start': start_time,
                'end': end_time,
                'label': pred_label,
                'probs': probs.cpu().numpy()
            })

    # 3. 印出時間軸上的變化
    print("\n--- 預測時間軸 (Timeline) ---")
    for p in predictions:
        prob_str = ", ".join([f"{config.IDX_2_LABEL[i]}: {prob:.2f}" for i, prob in enumerate(p['probs'])])
        print(f"[{p['start']:05.2f}s - {p['end']:05.2f}s] 預測: {p['label']:<5} | 機率分佈: [{prob_str}]")

    # 4. 統計整首音檔的最終結果 (透過平均所有視窗的機率)
    avg_probs = np.mean(all_probs, axis=0)
    final_pred_idx = np.argmax(avg_probs)
    final_label = config.IDX_2_LABEL[final_pred_idx]
    
    print("\n--- 整體加總預測結果 (Aggregated) ---")
    final_prob_str = ", ".join([f"{config.IDX_2_LABEL[i]}: {prob:.2f}" for i, prob in enumerate(avg_probs)])
    print(f"最終判定: ** {final_label} **")
    print(f"平均機率: [{final_prob_str}]\n")

    return final_label, predictions

if __name__ == '__main__':
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    model = ChordRefinerCNN(num_classes=len(config.CHORD_LIST)).to(device)
    model_path = 'best_chord_model.pth'
    
    if os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
        print(f"[Info] 成功載入模型權重: {model_path}")

        sample_wav_to_test = './record.m4a' 
        
        if os.path.exists(sample_wav_to_test):
            predict_audio(sample_wav_to_test, model, device, window_sec=2.0, hop_sec=0.5)
        else:
            print(f"[Warning] 找不到測試音檔 {sample_wav_to_test}。")
            
    else:
        print(f"[Error] 找不到模型權重檔案 ({model_path})")