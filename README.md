# IGTGS 自動吉他譜生成系統

`IGTGS` 是一個以 `Flask` 為後端、原生 `HTML/CSS/JavaScript` 為前端的自動吉他譜生成專案。  
使用者可以：

- 搜尋 `YouTube` 影片並下載音訊分析
- 直接上傳音檔分析
- 產生節拍同步的和弦譜
- 查看 `Guitar Chords` 指法圖與原始分析資料

## 環境需求

- 作業系統：Linux
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
