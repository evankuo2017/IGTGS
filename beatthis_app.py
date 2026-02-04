#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Beat This! Web Application - Pure HTML Interface
使用 Beat This! 模型進行精準的 beat 和 downbeat 追蹤（Flask 後端）
"""

from flask import Flask, request, jsonify, render_template_string
import os
import base64
import soundfile as sf
import numpy as np
from werkzeug.utils import secure_filename

# Flask 應用初始化
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['UPLOAD_FOLDER'] = '/tmp/beatthis_uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 全局變量存儲模型
_model = None

def get_model():
    """載入 Beat This! 模型"""
    global _model
    if _model is None:
        from beat_this.inference import File2Beats
        
        # 設定模型下載到當前目錄的 models 資料夾
        model_dir = os.path.join(os.getcwd(), 'models')
        os.makedirs(model_dir, exist_ok=True)
        os.environ['TORCH_HOME'] = model_dir
        
        print("Loading Beat This! model...")
        print(f"Model directory: {model_dir}")
        _model = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
        print("✓ Beat This! model loaded successfully")
    return _model

def estimate_time_signature(beats, downbeats):
    """根據每個小節內的 beat 數量推斷拍號"""
    if len(downbeats) < 2:
        return "4/4", 4
    
    bar_beat_counts = []
    
    for i in range(len(downbeats) - 1):
        # 計算每個小節內有幾個 beats
        start = downbeats[i]
        end = downbeats[i + 1]
        beats_in_bar = np.sum((beats >= start) & (beats < end))
        bar_beat_counts.append(beats_in_bar)
    
    # 找出最常見的拍數
    if bar_beat_counts:
        most_common = max(set(bar_beat_counts), key=bar_beat_counts.count)
        # 判斷拍號
        if most_common == 3:
            return "3/4", most_common
        elif most_common == 6:
            return "6/8", most_common
        elif most_common == 2:
            return "2/4", most_common
        else:
            return f"{most_common}/4", most_common
    
    return "4/4", 4  # 默認

def estimate_bpm(beats):
    """
    從 beats 估計 BPM（每分鐘拍數）
    使用中位數避免異常值影響
    """
    if len(beats) < 2:
        return 120.0  # 默認 BPM
    
    beats = np.array(beats)
    # 計算所有 beat 間隔
    intervals = np.diff(beats)
    
    # 移除異常值（過大或過小的間隔）
    median_interval = np.median(intervals)
    valid_intervals = intervals[(intervals > median_interval * 0.5) & 
                                (intervals < median_interval * 2.0)]
    
    if len(valid_intervals) == 0:
        avg_interval = median_interval
    else:
        avg_interval = np.median(valid_intervals)
    
    # BPM = 60秒 / 每拍秒數
    bpm = 60.0 / avg_interval
    return bpm

def refine_bars_with_time_signature(beats, downbeats, beats_per_bar):
    """
    用 BPM、拍號和 downbeats 精確切出小節位置
    基於時間間隔而非 beats 數量，避免漏檢/誤判影響
    
    參數:
        beats: 所有 beat 的時間點列表
        downbeats: Beat This! 預測的 downbeat 候選列表
        beats_per_bar: 每小節的拍數（例如 4/4 拍為 4）
    
    返回:
        bar_positions: 精確的小節起始位置列表
    """
    if len(beats) < 2:
        # beats 太少，回退到使用 downbeats
        return list(downbeats) if len(downbeats) > 0 else ([beats[0]] if len(beats) > 0 else [])
    
    beats = np.array(beats)
    downbeats = np.array(downbeats) if len(downbeats) > 0 else np.array([])
    
    # Step 1: 估計 BPM
    bpm = estimate_bpm(beats)
    print(f"  - Estimated BPM: {bpm:.1f}")
    
    # Step 2: 計算理論小節長度（秒）
    beat_duration = 60.0 / bpm  # 每拍的秒數
    bar_duration = beat_duration * beats_per_bar  # 每小節的秒數
    print(f"  - Theoretical bar duration: {bar_duration:.3f}s ({beats_per_bar} beats)")
    
    # Step 3: 確定起始點（使用第一個 downbeat 或第一個 beat）
    if len(downbeats) > 0:
        start_time = downbeats[0]
        print(f"  - Starting from first downbeat: {start_time:.3f}s")
    else:
        start_time = beats[0]
        print(f"  - Starting from first beat: {start_time:.3f}s")
    
    # Step 4: 找到音頻結束時間
    if len(beats) > 0:
        end_time = beats[-1] + bar_duration  # 延伸到最後
    else:
        end_time = start_time + bar_duration * 10  # 默認10個小節
    
    # Step 5: 按固定時間間隔生成理論小節位置
    bar_positions = []
    current_time = start_time
    bar_count = 0
    
    while current_time < end_time:
        theoretical_bar_start = current_time
        
        # Step 6: 在 Beat This! downbeats 中找最接近的候選做微調
        if len(downbeats) > 0:
            distances = np.abs(downbeats - theoretical_bar_start)
            closest_idx = np.argmin(distances)
            closest_downbeat = downbeats[closest_idx]
            min_distance = distances[closest_idx]
            
            # 動態容忍度：根據 BPM 調整
            # BPM 越快，容忍度越小
            tolerance = min(0.15, beat_duration * 0.3)  # 最多偏移 30% 一拍
            
            if min_distance < tolerance:
                # 使用 Beat This! 的 downbeat（更準確的韻律感知）
                bar_positions.append(float(closest_downbeat))
                # 從這個修正後的位置繼續，避免累積誤差
                current_time = closest_downbeat + bar_duration
            else:
                # 使用理論位置（Beat This! 可能漏檢或誤判）
                bar_positions.append(float(theoretical_bar_start))
                current_time += bar_duration
        else:
            # 沒有 downbeats，純用理論位置
            bar_positions.append(float(theoretical_bar_start))
            current_time += bar_duration
        
        bar_count += 1
        
        # 安全限制：最多1000個小節
        if bar_count > 1000:
            break
    
    print(f"✓ Bar refinement:")
    print(f"  - Original downbeats: {len(downbeats)}")
    print(f"  - Refined bar positions: {len(bar_positions)}")
    print(f"  - Average bar duration: {bar_duration:.3f}s")
    
    return bar_positions

# HTML 模板（與 wavebeat ui_app.py 相同，但加入拍號顯示）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Beat This! - Audio Beat Tracker</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 48px;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        
        .badge {
            display: inline-block;
            background: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 14px;
            margin-top: 10px;
        }
        
        .upload-section {
            background: white;
            border-radius: 15px;
            padding: 40px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
            text-align: center;
        }
        
        .upload-area {
            border: 3px dashed #667eea;
            border-radius: 10px;
            padding: 40px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .upload-area:hover {
            background: #f8f9fa;
            border-color: #764ba2;
        }
        
        .upload-area.dragover {
            background: #e9ecef;
            border-color: #28a745;
        }
        
        #fileInput {
            display: none;
        }
        
        .btn {
            padding: 15px 40px;
            border: none;
            border-radius: 50px;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0,0,0,0.3);
        }
        
        .btn-primary {
            background: #667eea;
            color: white;
        }
        
        .btn-success {
            background: #28a745;
            color: white;
        }
        
        .player-section {
            display: none;
            background: white;
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        
        .player-section.active {
            display: block;
        }
        
        .waveform-container {
            position: relative;
            height: 200px;
            background: #f8f9fa;
            border-radius: 10px;
            margin: 20px 0;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.1);
        }
        
        #waveform {
            width: 100%;
            height: 100%;
            display: block;
            cursor: pointer;
        }
        
        .controls {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin: 25px 0;
        }
        
        .info-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
            margin: 25px 0;
        }
        
        .info-card {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            border-left: 4px solid #667eea;
        }
        
        .info-card h3 {
            color: #667eea;
            margin-bottom: 10px;
            font-size: 16px;
        }
        
        .time-display {
            font-family: 'Courier New', monospace;
            font-size: 28px;
            font-weight: bold;
            color: #333;
        }
        
        .current-bar {
            font-size: 36px;
            font-weight: bold;
            color: #dc3545;
        }
        
        .time-sig-display {
            font-size: 42px;
            font-weight: bold;
            color: #667eea;
        }
        
        .bar-list-container {
            margin-top: 30px;
        }
        
        .bar-list-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            border-radius: 10px 10px 0 0;
            font-size: 18px;
            font-weight: bold;
        }
        
        .bar-list {
            max-height: 400px;
            overflow-y: auto;
            background: #f8f9fa;
            border-radius: 0 0 10px 10px;
            padding: 10px;
        }
        
        .bar-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 20px;
            margin: 5px 0;
            background: white;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            border-left: 4px solid transparent;
        }
        
        .bar-item:hover {
            background: #e9ecef;
            transform: translateX(5px);
            border-left-color: #667eea;
        }
        
        .bar-item.active {
            background: #dc3545;
            color: white;
            border-left-color: #dc3545;
            font-weight: bold;
        }
        
        .bar-number {
            font-size: 18px;
            font-weight: bold;
            color: #dc3545;
        }
        
        .bar-item.active .bar-number {
            color: white;
        }
        
        .loading {
            display: none;
            text-align: center;
            padding: 20px;
        }
        
        .loading.active {
            display: block;
        }
        
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .legend {
            display: flex;
            justify-content: center;
            gap: 30px;
            margin: 15px 0;
            font-size: 14px;
        }
        
        .legend-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .legend-color {
            width: 30px;
            height: 4px;
            border-radius: 2px;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }
        
        .stat-box {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        
        .stat-value {
            font-size: 32px;
            font-weight: bold;
            margin: 10px 0;
        }
        
        .stat-label {
            font-size: 14px;
            opacity: 0.9;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Beat This! Audio Analyzer</h1>
            <p style="font-size: 20px;">State-of-the-Art Beat Tracking with Accurate Bar Segmentation</p>
            <div class="badge">Powered by Beat This! Model</div>
        </div>
        
        <div class="upload-section">
            <h2 style="margin-bottom: 20px;">Upload Audio File</h2>
            <div class="upload-area" id="uploadArea">
                <div style="font-size: 64px; margin-bottom: 15px; color: #667eea; font-weight: bold;">♪</div>
                <p style="font-size: 20px; color: #333; margin-bottom: 15px; font-weight: bold;">
                    Drag & Drop Your Audio File Here
                </p>
                <p style="color: #666; margin-bottom: 10px;">or click to browse</p>
                <p style="color: #999; font-size: 14px;">Supports: WAV, MP3, FLAC (max 50MB)</p>
                <input type="file" id="fileInput" accept="audio/*">
            </div>
            
            <div class="loading" id="loading">
                <div class="spinner"></div>
                <p style="font-size: 20px; color: #667eea; font-weight: bold;">Analyzing with Beat This! model...</p>
                <p style="color: #999; margin-top: 10px;">Using advanced Transformer + CNN architecture...</p>
            </div>
        </div>
        
        <div class="player-section" id="playerSection">
            <h2 style="margin-bottom: 20px; color: #667eea;">Audio Player with Beat Visualization</h2>
            
            <div class="stats-grid" id="statsGrid" style="display:none;">
                <div class="stat-box" style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);">
                    <div class="stat-label" style="font-size: 16px; font-weight: bold;">Total Bars</div>
                    <div class="stat-value" id="totalBars" style="font-size: 42px;">0</div>
                </div>
                <div class="stat-box" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                    <div class="stat-label" style="font-size: 16px; font-weight: bold;">Time Signature</div>
                    <div class="stat-value" id="timeSig" style="font-size: 42px;">4/4</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Duration</div>
                    <div class="stat-value" id="totalDuration">0:00</div>
                </div>
                <div class="stat-box" style="opacity: 0.7;">
                    <div class="stat-label" style="font-size: 12px;">Total Beats</div>
                    <div class="stat-value" id="totalBeats" style="font-size: 24px;">0</div>
                </div>
            </div>
            
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color" style="background: #dc3545; height: 6px; width: 50px;"></div>
                    <strong style="font-size: 16px;">Bar Lines (精確小節位置)</strong>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: #28a745; height: 6px; width: 50px;"></div>
                    <strong style="font-size: 16px;">Current Position (播放位置)</strong>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background: rgba(200,200,200,0.3); width: 40px;"></div>
                    <span style="font-size: 12px; color: #999;">Beats (拍子)</span>
                </div>
            </div>
            
            <div class="waveform-container">
                <canvas id="waveform"></canvas>
            </div>
            
            <div class="controls">
                <button class="btn btn-success" id="playBtn">▶ Play</button>
                <button class="btn btn-primary" id="pauseBtn">⏸ Pause</button>
                <button class="btn" style="background:#dc3545;color:white" id="stopBtn">⏹ Stop</button>
            </div>
            
            <div class="info-grid">
                <div class="info-card">
                    <h3>PLAYBACK TIME</h3>
                    <div class="time-display" id="timeDisplay">0:00 / 0:00</div>
                </div>
                <div class="info-card" style="border-left-color: #dc3545;">
                    <h3 style="color: #dc3545; font-size: 18px;">CURRENT BAR</h3>
                    <div class="current-bar" id="barDisplay">-</div>
                </div>
                <div class="info-card" style="border-left-color: #667eea;">
                    <h3 style="color: #667eea; font-size: 18px;">TIME SIGNATURE</h3>
                    <div class="time-sig-display" id="timeSigDisplay">-</div>
                </div>
            </div>
            
            <div class="bar-list-container">
                <div class="bar-list-header" style="font-size: 20px;">
                    All Bars - Click to Jump
                </div>
                <div class="bar-list" id="barList"></div>
            </div>
        </div>
    </div>
    
    <audio id="audio" preload="auto"></audio>
    
    <script>
        console.log('Beat This! UI initialized');
        
        const uploadArea = document.getElementById('uploadArea');
        const fileInput = document.getElementById('fileInput');
        const loading = document.getElementById('loading');
        const playerSection = document.getElementById('playerSection');
        const canvas = document.getElementById('waveform');
        const ctx = canvas.getContext('2d');
        const audio = document.getElementById('audio');
        const playBtn = document.getElementById('playBtn');
        const pauseBtn = document.getElementById('pauseBtn');
        const stopBtn = document.getElementById('stopBtn');
        const timeDisplay = document.getElementById('timeDisplay');
        const barDisplay = document.getElementById('barDisplay');
        const timeSigDisplay = document.getElementById('timeSigDisplay');
        const barList = document.getElementById('barList');
        const statsGrid = document.getElementById('statsGrid');
        const totalBeats = document.getElementById('totalBeats');
        const totalBars = document.getElementById('totalBars');
        const totalDuration = document.getElementById('totalDuration');
        const timeSig = document.getElementById('timeSig');
        
        let beats = [];
        let downbeats = [];
        let duration = 0;
        let timeSignature = "4/4";
        let isPlaying = false;
        let currentBar = -1;
        
        console.log('All elements loaded');
        
        // 上傳區域事件
        uploadArea.onclick = () => {
            console.log('Upload area clicked');
            fileInput.click();
        };
        
        uploadArea.ondragover = (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        };
        
        uploadArea.ondragleave = () => {
            uploadArea.classList.remove('dragover');
        };
        
        uploadArea.ondrop = (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                console.log('File dropped:', e.dataTransfer.files[0].name);
                handleFile(e.dataTransfer.files[0]);
            }
        };
        
        fileInput.onchange = (e) => {
            if (e.target.files.length > 0) {
                console.log('File selected:', e.target.files[0].name);
                handleFile(e.target.files[0]);
            }
        };
        
        async function handleFile(file) {
            console.log('Starting file analysis:', file.name);
            loading.classList.add('active');
            playerSection.classList.remove('active');
            statsGrid.style.display = 'none';
            
            const formData = new FormData();
            formData.append('audio', file);
            
            try {
                console.log('Sending request to /analyze...');
                const response = await fetch('/analyze', {
                    method: 'POST',
                    body: formData
                });
                
                console.log('Response received:', response.status);
                const data = await response.json();
                
                if (data.error) {
                    console.error('Server error:', data.error);
                    alert('Error: ' + data.error);
                    loading.classList.remove('active');
                    return;
                }
                
                console.log('Data received:', {
                    beats: data.beats.length,
                    downbeats: data.downbeats.length,
                    duration: data.duration,
                    time_signature: data.time_signature
                });
                
                // 設置數據
                beats = data.beats;
                downbeats = data.downbeats;
                duration = data.duration;
                timeSignature = data.time_signature;
                
                console.log('Beats:', beats);
                console.log('Bar Positions (refined):', downbeats);
                console.log('Time Signature:', timeSignature);
                
                // 更新統計
                totalBeats.textContent = beats.length;
                totalBars.textContent = downbeats.length;
                totalDuration.textContent = formatTime(duration);
                timeSig.textContent = timeSignature;
                timeSigDisplay.textContent = timeSignature;
                statsGrid.style.display = 'grid';
                
                // 設置音頻
                audio.src = 'data:' + data.audio_mime + ';base64,' + data.audio_data;
                console.log('Audio source set');
                
                // 顯示播放器
                loading.classList.remove('active');
                playerSection.classList.add('active');
                
                // 初始化顯示
                resizeCanvas();
                createBarList();
                timeDisplay.textContent = '0:00 / ' + formatTime(duration);
                barDisplay.textContent = '-';
                
                console.log('UI initialized successfully');
                
            } catch (err) {
                console.error('Error during analysis:', err);
                alert('Error analyzing audio: ' + err.message);
                loading.classList.remove('active');
            }
        }
        
        function resizeCanvas() {
            const rect = canvas.getBoundingClientRect();
            canvas.width = rect.width;
            canvas.height = rect.height;
            console.log('Canvas resized:', canvas.width, 'x', canvas.height);
            drawWaveform();
        }
        
        function drawWaveform() {
            const w = canvas.width;
            const h = canvas.height;
            
            // 清空背景
            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = '#f8f9fa';
            ctx.fillRect(0, 0, w, h);
            
            // 繪製時間刻度背景網格
            ctx.strokeStyle = '#f0f0f0';
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            for (let i = 0; i <= 10; i++) {
                const x = (w / 10) * i;
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, h);
                ctx.stroke();
            }
            
            // 繪製 Beats (非常淡的灰色短虛線，只在上半部)
            ctx.strokeStyle = 'rgba(200, 200, 200, 0.25)';
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 8]);
            beats.forEach(beat => {
                const x = (beat / duration) * w;
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, h * 0.4);
                ctx.stroke();
            });
            
            // 繪製精確的小節線 (Bar Lines - 紅色實線，粗線)
            // 這些位置是由拍號和 beats 精確計算得出
            ctx.setLineDash([]);
            ctx.strokeStyle = '#dc3545';
            ctx.lineWidth = 6;
            downbeats.forEach((db, i) => {
                const x = (db / duration) * w;
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, h);
                ctx.stroke();
                
                // 標記小節編號
                ctx.fillStyle = '#dc3545';
                ctx.font = 'bold 18px Arial';
                ctx.textAlign = 'center';
                ctx.fillText('Bar ' + (i + 1), x, h - 12);
            });
            
            // 繪製播放位置 (綠色線)
            if (isPlaying && audio.currentTime > 0) {
                const x = (audio.currentTime / duration) * w;
                ctx.strokeStyle = '#28a745';
                ctx.lineWidth = 4;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, h);
                ctx.stroke();
            }
        }
        
        function createBarList() {
            barList.innerHTML = '';
            console.log('Creating bar list with', downbeats.length, 'accurate bar positions');
            downbeats.forEach((time, i) => {
                const div = document.createElement('div');
                div.className = 'bar-item';
                div.id = 'bar-' + i;
                div.innerHTML = `
                    <span class="bar-number">Bar ${i + 1}</span>
                    <span style="font-family: monospace; font-size: 16px;">${formatTime(time)}</span>
                `;
                div.onclick = () => {
                    console.log('Jumping to bar', i + 1, 'at', time);
                    audio.currentTime = time;
                    if (!isPlaying) drawWaveform();
                };
                barList.appendChild(div);
            });
        }
        
        function update() {
            if (isPlaying) {
                timeDisplay.textContent = formatTime(audio.currentTime) + ' / ' + formatTime(duration);
                
                // 找到當前小節
                let newBar = -1;
                for (let i = 0; i < downbeats.length; i++) {
                    if (audio.currentTime >= downbeats[i]) {
                        newBar = i;
                    }
                }
                
                // 更新高亮
                if (newBar !== currentBar) {
                    if (currentBar >= 0) {
                        const oldElem = document.getElementById('bar-' + currentBar);
                        if (oldElem) oldElem.classList.remove('active');
                    }
                    if (newBar >= 0) {
                        const newElem = document.getElementById('bar-' + newBar);
                        if (newElem) {
                            newElem.classList.add('active');
                            newElem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                        }
                    }
                    currentBar = newBar;
                }
                
                barDisplay.textContent = currentBar >= 0 ? ('Bar ' + (currentBar + 1)) : '-';
                
                drawWaveform();
                requestAnimationFrame(update);
            }
        }
        
        function formatTime(sec) {
            const m = Math.floor(sec / 60);
            const s = Math.floor(sec % 60);
            return m + ':' + String(s).padStart(2, '0');
        }
        
        playBtn.onclick = () => {
            console.log('Play button clicked');
            audio.play();
            isPlaying = true;
            update();
        };
        
        pauseBtn.onclick = () => {
            console.log('Pause button clicked');
            audio.pause();
            isPlaying = false;
        };
        
        stopBtn.onclick = () => {
            console.log('Stop button clicked');
            audio.pause();
            audio.currentTime = 0;
            isPlaying = false;
            currentBar = -1;
            document.querySelectorAll('.bar-item').forEach(el => el.classList.remove('active'));
            timeDisplay.textContent = '0:00 / ' + formatTime(duration);
            barDisplay.textContent = '-';
            drawWaveform();
        };
        
        canvas.onclick = (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const progress = x / rect.width;
            audio.currentTime = progress * duration;
            console.log('Canvas clicked, jumped to', audio.currentTime);
            if (!isPlaying) drawWaveform();
        };
        
        window.addEventListener('resize', resizeCanvas);
        
        audio.onended = () => {
            console.log('Audio ended');
            isPlaying = false;
            stopBtn.click();
        };
        
        audio.onerror = (e) => {
            console.error('Audio error:', e);
        };
        
        console.log('All event listeners attached');
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """主頁面 - 返回完整 HTML 界面"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/analyze', methods=['POST'])
def analyze():
    """分析音頻文件 API"""
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    
    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # 保存文件
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        print(f"Analyzing file: {filepath}")
        
        # 載入模型並分析
        model = get_model()
        
        # 使用 Beat This! 進行預測
        beats, downbeats = model(filepath)
        
        # 將 numpy array 轉為 Python list（確保元素也是 Python 原生類型）
        beats = [float(b) for b in beats]
        downbeats = [float(d) for d in downbeats]
        
        print(f"✓ Analysis complete:")
        print(f"  - Beats: {len(beats)}")
        print(f"  - Original Downbeats: {len(downbeats)}")
        
        # 估計拍號
        time_signature, beats_per_bar = estimate_time_signature(np.array(beats), np.array(downbeats))
        beats_per_bar = int(beats_per_bar)  # 確保是 Python int
        print(f"  - Estimated Time Signature: {time_signature} ({beats_per_bar} beats per bar)")
        
        # 用拍號和 beats 精確切出小節位置（修正 downbeats）
        bar_positions = refine_bars_with_time_signature(beats, downbeats, beats_per_bar)
        
        # 獲取音頻信息
        audio_info = sf.info(filepath)
        duration = float(audio_info.duration)  # 確保是 Python float
        print(f"  - Duration: {duration:.2f}s")
        print(f"  - Total Bars: {len(bar_positions)}")
        
        # 轉換音頻為 base64
        with open(filepath, 'rb') as f:
            audio_data = base64.b64encode(f.read()).decode('utf-8')
        
        # 判斷 MIME 類型
        if filepath.lower().endswith('.mp3'):
            audio_mime = 'audio/mpeg'
        elif filepath.lower().endswith('.wav'):
            audio_mime = 'audio/wav'
        elif filepath.lower().endswith('.flac'):
            audio_mime = 'audio/flac'
        else:
            audio_mime = 'audio/mpeg'
        
        # 清理臨時文件
        os.remove(filepath)
        
        # 返回結果（downbeats 改為 bar_positions）
        result = {
            'beats': beats,
            'downbeats': bar_positions,  # 現在是精確的小節位置
            'duration': duration,
            'time_signature': time_signature,
            'beats_per_bar': beats_per_bar,
            'audio_data': audio_data,
            'audio_mime': audio_mime
        }
        
        print(f"✓ Returning result to client")
        return jsonify(result)
        
    except Exception as e:
        import traceback
        print("ERROR during analysis:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 70)
    print("Beat This! Web Application")
    print("=" * 70)
    print("\n[*] State-of-the-Art Beat Tracking (Beat This! - ISMIR 2024)")
    print("[*] Automatic Time Signature Estimation")
    print("[*] Accurate Bar Segmentation (Bar-level precision)")
    print("[*] Real-time synchronized playback")
    print("\n[Server Information]")
    print("  - URL: http://localhost:5000")
    print("  - Model: Beat This! final0")
    print("  - Device: CPU")
    print("\n[Instructions]")
    print("  1. Open your browser and navigate to http://localhost:5000")
    print("  2. Upload an audio file (WAV, MP3, or FLAC)")
    print("  3. Wait for analysis to complete")
    print("  4. View accurate bar segmentation results")
    print("\n[Control]")
    print("  Press Ctrl+C to stop the server")
    print("=" * 70 + "\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
