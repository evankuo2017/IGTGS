"""ChordRefiner 推理與 CQT 參數（與訓練時類別／頻譜設定一致）。"""

# 四類 quality（與主和弦辨識 submission 字典對齊之子集）
CHORD_LIST = ["maj", "maj7", "min", "min7"]
LABEL_2_IDX = {chord: idx for idx, chord in enumerate(CHORD_LIST)}
IDX_2_LABEL = {idx: chord for idx, chord in enumerate(CHORD_LIST)}

SR = 22050
HOP_LENGTH = 512
N_BINS = 84
BINS_PER_OCTAVE = 12
