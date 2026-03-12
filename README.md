# IGTGS 自動吉他譜生成系統

`IGTGS` 是一個以 `Flask` 為後端、原生 `HTML/CSS/JavaScript` 為前端的自動吉他譜生成專案。  
使用者可以：

- 搜尋 `YouTube` 影片並下載音訊分析
- 直接上傳音檔分析
- 產生節拍同步的和弦譜
- 查看 `Guitar Chords` 指法圖與原始分析資料

## 環境需求

- 作業系統：Linux / macOS
- Python：建議 `3.10`
- `conda` 或 `miniconda`
- `ffmpeg`

## 1. 建立 conda 環境

在專案外或任意終端機執行：

```bash
conda create -n IGTGS python=3.10 -y
conda activate IGTGS
```

如果你之前已有同名環境，想先刪除再重建：

```bash
conda deactivate
conda env remove -n IGTGS
conda create -n IGTGS python=3.10 -y
conda activate IGTGS
```

## 2. 安裝 ffmpeg

`YouTube` 音訊下載與部分音訊處理流程需要 `ffmpeg`。

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y ffmpeg
```

### Conda 安裝方式

如果你想直接裝在 conda 環境內：

```bash
conda install -c conda-forge ffmpeg -y
```

## 3. 安裝 Python 依賴

先切換到專案資料夾：

```bash
cd /home/samjimbe/project/IGTGS
```

再安裝依賴：

```bash
pip install -r requirements.txt
```

## 4. 啟動系統

在 `IGTGS` 專案根目錄執行：

```bash
python app.py
```

預設啟動位置：

- 網址：[http://127.0.0.1:5055](http://127.0.0.1:5055)
- Port：`5055`

如果你是在同一台機器本地操作，也可以用：

- [http://localhost:5055](http://localhost:5055)

## 5. 使用方式

### 方法 A：搜尋 YouTube

1. 在首頁輸入 `YouTube` 關鍵字或影片連結
2. 選擇搜尋結果
3. 選擇 `Beat Detector` 與 `Chord Detector`
4. 點擊分析

### 方法 B：上傳音檔

1. 選擇本地音檔
2. 選擇 `Beat Detector` 與 `Chord Detector`
3. 點擊分析

分析完成後可查看：

- `整首歌和弦譜`
- `全部和弦指法圖`
- `Raw Data`

## 6. 專案結構

```text
IGTGS/
├── app.py
├── analysis_engine.py
├── grid_builder.py
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   ├── app.js
│   ├── styles.css
│   ├── vendor/chords/
│   └── chord-diagrams/
├── igtgs_backend/
└── runtime/
```

主要檔案用途：

- `app.py`：Flask 入口、API、YouTube 搜尋與音訊下載
- `analysis_engine.py`：音訊分析流程整合
- `grid_builder.py`：把 beat / chord 結果整理成前端可顯示的譜面資料
- `static/app.js`：前端互動、和弦格式化、播放同步
- `static/styles.css`：整體 UI 樣式

## 7. 常見問題

### 1. `ModuleNotFoundError: No module named 'flask'`

代表目前不是在正確環境中，請先：

```bash
conda activate IGTGS
pip install -r requirements.txt
```

### 2. YouTube 搜尋或下載失敗

請確認：

- 網路正常
- `yt-dlp` 可正常使用
- `ffmpeg` 已安裝

### 3. 啟動後頁面打不開

請確認是否成功看到類似訊息：

```bash
* Running on http://127.0.0.1:5055
```

若 `5055` 被佔用，可先停止其他程式後再重啟。

### 4. 模型安裝很久

此專案依賴 `tensorflow`、`torch`、`librosa`、`madmom` 等較大型套件，第一次安裝時間較長是正常現象。

## 8. 開發備註

- 本專案前端不使用框架，採原生 `JavaScript`
- 後端使用 `Flask`
- 分析完成後，系統會把音訊暫存到 `runtime/audio_cache/`
- 和弦顯示規則與 UI 呈現已針對本專案額外做格式化處理，例如：
  - `maj7 -> Δ7`
  - `min -> m`
  - `dim -> °`
  - `aug -> +`
  - `/3`、`/b7` 轉成實際 bass note

## 9. 快速重跑

如果你之後只想快速啟動：

```bash
conda activate IGTGS
cd /home/samjimbe/project/IGTGS
python app.py
```
