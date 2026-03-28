import os

# Paths
DATASET_DIR = './dataset'
RAW_DATA_DIR = './data2'
GUITAR_LAB = os.path.join(RAW_DATA_DIR, 'guitar/guitar_annotation.lab')
NON_GUITAR_LAB = os.path.join(RAW_DATA_DIR, 'non_guitar/non_guitar_annotation.lab')

# Categories
CHORD_LIST = ['maj', 'maj7', 'min', 'min7']
LABEL_2_IDX = {chord: idx for idx, chord in enumerate(CHORD_LIST)}
IDX_2_LABEL = {idx: chord for idx, chord in enumerate(CHORD_LIST)}

# Audio & CQT Parameters
SR = 22050
HOP_LENGTH = 512
N_BINS = 84
BINS_PER_OCTAVE = 12

# Training Parameters
BATCH_SIZE = 32
EPOCHS = 50
LEARNING_RATE = 1e-4