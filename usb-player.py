r"""
USB Audio Player + YouTube MP3 Downloader
Run this script, then open http://localhost:8888 in your browser.
Downloads YouTube videos as MP3 directly to your USB drive (F:\).
"""

import http.server
import json
import subprocess
import threading
import os
import re
import webbrowser
import shutil
import urllib.parse
import string
import ctypes
import ctypes.wintypes
import sys

USB_DRIVE = "F:\\"
PORT = 8888


def pick_folder():
    """Open a native Windows 'Browse for Folder' dialog and return the selected path."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(title="Select folder to save MP3s")
        root.destroy()
        if folder:
            return folder.replace('/', '\\')
        return None
    except Exception as e:
        print(f"[ERROR] Folder picker failed: {e}")
        return None


def get_drives():
    """List available drives on Windows with labels."""
    drives = []
    if os.name == 'nt':
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                path = f"{letter}:\\"
                try:
                    label_buf = ctypes.create_unicode_buffer(256)
                    ctypes.windll.kernel32.GetVolumeInformationW(
                        path, label_buf, 256, None, None, None, None, 0
                    )
                    label = label_buf.value or ""
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(path)
                    # 2=Removable, 3=Fixed, 4=Network, 5=CDROM, 6=RAMDisk
                    type_name = {2: "USB", 3: "Local", 4: "Network", 5: "CD", 6: "RAM"}.get(drive_type, "")
                    drives.append({
                        'letter': letter,
                        'path': path,
                        'label': label,
                        'type': type_name,
                        'removable': drive_type == 2,
                    })
                except Exception:
                    drives.append({'letter': letter, 'path': path, 'label': '', 'type': '', 'removable': False})
    return drives

# Find yt-dlp. Prefer the WinGet standalone binary over the pip shim, because
# the pip shim re-enters the host Python and breaks if any dep (e.g. chardet's
# C extension) is incompatible with the installed interpreter.
YTDLP_PATH = None
winget_base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
if os.path.isdir(winget_base):
    for d in os.listdir(winget_base):
        if "yt-dlp" in d.lower():
            candidate_dir = os.path.join(winget_base, d)
            for root, dirs, files in os.walk(candidate_dir):
                if "yt-dlp.exe" in files:
                    YTDLP_PATH = os.path.join(root, "yt-dlp.exe")
                    break
            if YTDLP_PATH:
                break

if not YTDLP_PATH:
    YTDLP_PATH = shutil.which("yt-dlp")
if not YTDLP_PATH:
    scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
    candidate = os.path.join(scripts_dir, "yt-dlp.exe")
    if os.path.isfile(candidate):
        YTDLP_PATH = candidate

# Find ffmpeg
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    # Check WinGet install location
    winget_base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if os.path.isdir(winget_base):
        for d in os.listdir(winget_base):
            if "ffmpeg" in d.lower():
                candidate = os.path.join(winget_base, d)
                for root, dirs, files in os.walk(candidate):
                    if "ffmpeg.exe" in files:
                        FFMPEG_PATH = os.path.join(root, "ffmpeg.exe")
                        break

print(f"yt-dlp: {YTDLP_PATH or 'NOT FOUND'}")
print(f"FFmpeg: {FFMPEG_PATH or 'NOT FOUND'}")
print(f"USB Drive: {USB_DRIVE}")

# Track active downloads
downloads = {}
download_id = 0
download_lock = threading.Lock()

# Download history
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
HISTORY_FILE = os.path.join(SCRIPT_DIR, 'download_history.json')
download_history = []


def load_history():
    global download_history
    try:
        if os.path.isfile(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                download_history = json.load(f)
    except Exception:
        download_history = []


def save_history():
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(download_history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


load_history()

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>USB Audio Player + YouTube Downloader</title>
<style>
  :root {
    --bg: #0c0d1f;
    --bg-elev-1: #15172e;
    --bg-elev-2: #1b1e3a;
    --bg-input: #0a0b1a;
    --border: #262a4d;
    --border-soft: #1f2240;
    --text: #e8e9f3;
    --text-dim: #8a8fad;
    --text-faint: #555a7a;
    --accent: #ff4d6d;
    --accent-hover: #ff6b85;
    --accent-soft: rgba(255, 77, 109, 0.12);
    --accent-glow: rgba(255, 77, 109, 0.32);
    --success: #2ecc71;
    --warning: #f5a623;
    --danger: #e74c3c;
    --radius: 14px;
    --radius-sm: 8px;
    --shadow: 0 14px 44px rgba(0, 0, 0, 0.5);
    --ease: cubic-bezier(0.4, 0, 0.2, 1);
    --mono: 'Consolas', 'SF Mono', ui-monospace, monospace;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }
  *::selection { background: var(--accent-soft); color: var(--text); }

  html, body { height: 100%; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: radial-gradient(ellipse at top, #1a1c3a 0%, #0c0d1f 55%);
    color: var(--text);
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding: 40px 24px;
    gap: 28px;
    flex-wrap: wrap;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }

  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: #353a66; }

  .panel {
    background: linear-gradient(180deg, var(--bg-elev-1) 0%, #121429 100%);
    border-radius: var(--radius);
    padding: 28px;
    width: 460px;
    box-shadow: var(--shadow);
    border: 1px solid var(--border-soft);
    align-self: flex-start;
  }

  h1 {
    text-align: center;
    font-size: 11px;
    font-weight: 600;
    color: var(--accent);
    margin-bottom: 24px;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    position: relative;
    padding-bottom: 14px;
  }
  h1::after {
    content: '';
    position: absolute;
    bottom: 0; left: 50%;
    transform: translateX(-50%);
    width: 36px; height: 2px;
    background: var(--accent);
    border-radius: 1px;
    opacity: 0.5;
  }

  .label {
    font-size: 10px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 1.4px;
    font-weight: 600;
    display: block;
    margin-bottom: 8px;
  }

  /* INPUTS */
  input[type="text"] {
    width: 100%;
    padding: 12px 16px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    background: var(--bg-input);
    color: var(--text);
    font-size: 14px;
    outline: none;
    transition: border-color 0.2s var(--ease), box-shadow 0.2s var(--ease);
    font-family: inherit;
  }
  input[type="text"]:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  input[type="text"]::placeholder { color: var(--text-faint); }

  /* BUTTONS */
  .btn-primary, .btn-secondary, .btn-ghost {
    padding: 11px 20px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    cursor: pointer;
    font-weight: 600;
    white-space: nowrap;
    font-family: inherit;
    transition: all 0.2s var(--ease);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
  }
  .btn-primary {
    background: var(--accent);
    color: white;
    border: none;
    box-shadow: 0 4px 14px var(--accent-glow);
  }
  .btn-primary:hover { background: var(--accent-hover); transform: translateY(-1px); box-shadow: 0 6px 18px var(--accent-glow); }
  .btn-primary:active { transform: translateY(0); }
  .btn-primary:disabled {
    background: var(--border);
    color: var(--text-faint);
    box-shadow: none;
    cursor: not-allowed;
    transform: none;
  }

  .btn-secondary {
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover { border-color: var(--accent); color: var(--accent); }

  .btn-ghost {
    background: transparent;
    color: var(--text-dim);
    border: 1px solid var(--border-soft);
    padding: 7px 12px;
    font-size: 11px;
    letter-spacing: 0.3px;
  }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }

  .btn-block { width: 100%; }

  /* DRIVE PICKER */
  .drive-picker {
    margin-bottom: 18px;
    padding: 14px;
    background: var(--bg-input);
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-soft);
  }
  .drive-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .drive-path {
    font-size: 12px;
    color: var(--text);
    word-break: break-all;
    font-family: var(--mono);
    flex: 1;
  }

  /* DOWNLOAD */
  .yt-input-row {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }
  .yt-input-row input { flex: 1; }

  .dl-queue { margin-top: 10px; }
  .dl-item {
    background: var(--bg-input);
    border-radius: var(--radius-sm);
    padding: 12px 14px;
    margin-bottom: 8px;
    border: 1px solid var(--border-soft);
    transition: border-color 0.2s var(--ease);
    animation: slideIn 0.3s var(--ease);
  }
  @keyframes slideIn {
    from { opacity: 0; transform: translateY(-6px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .dl-item .dl-title {
    font-size: 13px;
    margin-bottom: 8px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-weight: 500;
  }
  .dl-item .dl-bar {
    height: 4px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 2px;
    overflow: hidden;
  }
  .dl-item .dl-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent-hover));
    border-radius: 2px;
    transition: width 0.3s var(--ease);
    box-shadow: 0 0 8px var(--accent-glow);
  }
  .dl-item .dl-status {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 6px;
  }
  .dl-item.done { border-color: var(--success); }
  .dl-item.done .dl-bar-fill { background: var(--success); box-shadow: 0 0 8px rgba(46, 204, 113, 0.4); }
  .dl-item.error { border-color: var(--danger); }
  .dl-item.error .dl-status { color: var(--danger); }

  /* SECTIONS */
  .section {
    margin-top: 22px;
    padding-top: 18px;
    border-top: 1px solid var(--border-soft);
  }
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    gap: 10px;
  }
  .section-header h2 {
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 1.4px;
    font-weight: 600;
  }
  .section-actions { display: flex; gap: 6px; }

  /* USB FILES */
  .usb-file-list {
    max-height: 200px;
    overflow-y: auto;
    border-radius: var(--radius-sm);
    background: var(--bg-input);
    padding: 4px;
  }
  .usb-file-item {
    font-size: 12px;
    padding: 7px 10px;
    border-radius: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-dim);
    transition: background 0.15s var(--ease), color 0.15s var(--ease);
  }
  .usb-file-item:hover { background: rgba(255, 255, 255, 0.03); color: var(--text); }
  .empty-state {
    font-size: 12px;
    color: var(--text-faint);
    padding: 18px;
    text-align: center;
  }

  /* PLAYER DISPLAY */
  .display {
    background: linear-gradient(135deg, #0a0b1a 0%, #131530 100%);
    border-radius: var(--radius-sm);
    padding: 22px;
    margin-bottom: 22px;
    border: 1px solid var(--border-soft);
    position: relative;
    overflow: hidden;
  }
  .display::before {
    content: '';
    position: absolute;
    top: -60%; right: -20%;
    width: 220px; height: 220px;
    background: radial-gradient(circle, var(--accent-soft), transparent 60%);
    pointer-events: none;
  }
  .track-num {
    color: var(--accent);
    font-size: 10px;
    letter-spacing: 1.5px;
    font-weight: 600;
    text-transform: uppercase;
    position: relative;
  }
  .track-name {
    font-size: 16px;
    font-weight: 500;
    margin: 8px 0 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    position: relative;
  }
  .progress-bar {
    width: 100%;
    height: 5px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 3px;
    cursor: pointer;
    margin: 4px 0;
    position: relative;
    transition: height 0.15s var(--ease);
  }
  .progress-bar:hover { height: 7px; }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent-hover));
    border-radius: 3px;
    width: 0%;
    transition: width 0.1s linear;
    box-shadow: 0 0 8px var(--accent-glow);
  }
  .time-row {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 8px;
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    position: relative;
  }

  /* CONTROLS */
  .controls {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 14px;
    margin-bottom: 8px;
  }
  .btn {
    background: transparent;
    border: 1.5px solid var(--border);
    color: var(--text);
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s var(--ease);
    font-family: inherit;
  }
  .btn:hover {
    border-color: var(--accent);
    color: var(--accent);
    transform: scale(1.05);
  }
  .btn:active { transform: scale(0.96); }
  .btn-sm { width: 44px; height: 44px; font-size: 14px; }
  .btn-lg {
    width: 60px; height: 60px;
    font-size: 22px;
    background: var(--accent);
    border-color: var(--accent);
    color: white;
    box-shadow: 0 6px 20px var(--accent-glow);
  }
  .btn-lg:hover {
    background: var(--accent-hover);
    border-color: var(--accent-hover);
    color: white;
    transform: scale(1.06);
  }
  .btn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: white;
  }
  .loop-label {
    font-size: 11px;
    color: var(--text-dim);
    text-align: center;
    margin-top: 10px;
    letter-spacing: 0.5px;
  }

  /* VOLUME */
  .volume-row {
    display: flex;
    align-items: center;
    gap: 12px;
    justify-content: center;
    margin: 18px 0;
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 1px;
    font-weight: 600;
  }
  .volume-row input[type=range] {
    -webkit-appearance: none;
    appearance: none;
    width: 150px;
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    outline: none;
    cursor: pointer;
  }
  .volume-row input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px; height: 14px;
    background: var(--accent);
    border-radius: 50%;
    cursor: pointer;
    box-shadow: 0 0 8px var(--accent-glow);
    transition: transform 0.15s var(--ease);
  }
  .volume-row input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.25); }
  .volume-row input[type=range]::-moz-range-thumb {
    width: 14px; height: 14px;
    background: var(--accent);
    border: none;
    border-radius: 50%;
    cursor: pointer;
  }
  #volVal {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    min-width: 36px;
    color: var(--text);
  }

  /* PLAYLIST */
  .playlist {
    max-height: 320px;
    overflow-y: auto;
    border-radius: var(--radius-sm);
    background: var(--bg-input);
    padding: 4px;
  }
  .playlist-item {
    padding: 10px 12px;
    font-size: 13px;
    cursor: pointer;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 12px;
    transition: background 0.15s var(--ease), color 0.15s var(--ease);
    color: var(--text);
  }
  .playlist-item:hover { background: rgba(255, 77, 109, 0.06); }
  .playlist-item.active {
    background: var(--accent-soft);
    color: var(--accent);
  }
  .playlist-item.active .idx { color: var(--accent); }
  .playlist-item .idx {
    color: var(--text-faint);
    font-size: 11px;
    min-width: 26px;
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }
  .playlist-item .name {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }

  /* HISTORY */
  .history-list {
    max-height: 300px;
    overflow-y: auto;
    margin-top: 10px;
    display: none;
    background: var(--bg-input);
    border-radius: var(--radius-sm);
    padding: 4px;
  }
  .history-list.open { display: block; animation: slideIn 0.25s var(--ease); }
  .history-item {
    font-size: 12px;
    padding: 8px 12px;
    border-radius: 6px;
    color: var(--text-dim);
    transition: background 0.15s var(--ease), color 0.15s var(--ease);
  }
  .history-item:hover { background: rgba(255, 255, 255, 0.03); color: var(--text); }
  .history-item .history-idx {
    color: var(--accent);
    margin-right: 8px;
    font-size: 11px;
    font-family: var(--mono);
  }
  .history-empty { color: var(--text-faint); font-size: 12px; padding: 14px; text-align: center; }
</style>
</head>
<body>

<!-- LEFT: YouTube Downloader -->
<div class="panel">
  <h1>YouTube to USB</h1>

  <div class="drive-picker">
    <span class="label">Save Location</span>
    <div class="drive-row">
      <button class="btn-secondary" onclick="pickFolder()">Browse...</button>
      <span id="currentFolder" class="drive-path">Loading...</span>
    </div>
  </div>

  <div class="yt-input-row">
    <input type="text" id="ytUrl" placeholder="Paste YouTube URL...">
    <button class="btn-primary" id="dlBtn" onclick="startDownload()">Download</button>
  </div>

  <div class="dl-queue" id="dlQueue"></div>

  <div class="section">
    <div class="section-header">
      <h2 id="filesHeader">Files on USB</h2>
      <div class="section-actions">
        <button class="btn-ghost" onclick="loadUsbFiles()">Refresh</button>
        <button class="btn-ghost" onclick="renameAll()">Rename 100, 101...</button>
      </div>
    </div>
    <div class="usb-file-list" id="usbFileList">Loading...</div>
  </div>

  <div class="section">
    <button class="btn-secondary btn-block" onclick="toggleHistory()">Downloaded Videos</button>
    <div class="history-list" id="historyList"></div>
  </div>
</div>

<!-- RIGHT: Audio Player -->
<div class="panel">
  <h1>USB Audio Player</h1>

  <div class="display">
    <div class="track-num" id="trackNum">NO TRACKS LOADED</div>
    <div class="track-name" id="trackName">Click "Load USB Songs" to start</div>
    <div class="progress-bar" id="progressBar">
      <div class="progress-fill" id="progressFill"></div>
    </div>
    <div class="time-row">
      <span id="curTime">0:00</span>
      <span id="totalTime">0:00</span>
    </div>
  </div>

  <div class="controls">
    <button class="btn btn-sm" id="btnPrev" title="Previous">&#9198;</button>
    <button class="btn btn-lg" id="btnPlay" title="Play/Pause">&#9654;</button>
    <button class="btn btn-sm" id="btnNext" title="Next">&#9197;</button>
    <button class="btn btn-sm" id="btnLoop" title="Loop Mode">&#128257;</button>
  </div>
  <div class="loop-label" id="loopLabel">Loop: All</div>

  <div class="volume-row">
    <span>VOL</span>
    <input type="range" id="volume" min="0" max="100" value="80">
    <span id="volVal">80%</span>
  </div>

  <button class="btn-secondary btn-block" style="margin-bottom:14px;" onclick="loadUsbAudio()">Load USB Songs</button>
  <div class="playlist" id="playlist"></div>
</div>

<audio id="audio"></audio>

<script>
// ==================== DOWNLOADER ====================
function startDownload() {
  const url = document.getElementById('ytUrl').value.trim();
  if (!url) return;

  const btn = document.getElementById('dlBtn');
  btn.disabled = true;

  fetch('/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) {
      alert('Error: ' + data.error);
      btn.disabled = false;
      return;
    }
    document.getElementById('ytUrl').value = '';
    btn.disabled = false;
    if (data.playlist && data.entries) {
      data.entries.forEach(function(e) {
        addToQueue(e.id, e.title);
        pollDownload(e.id);
      });
    } else {
      addToQueue(data.id, data.title || url);
      pollDownload(data.id);
    }
  })
  .catch(e => {
    alert('Request failed: ' + e);
    btn.disabled = false;
  });
}

document.getElementById('ytUrl').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') startDownload();
});

function addToQueue(id, title) {
  const q = document.getElementById('dlQueue');
  const div = document.createElement('div');
  div.className = 'dl-item';
  div.id = 'dl-' + id;
  div.innerHTML =
    '<div class="dl-title">' + escapeHtml(title) + '</div>' +
    '<div class="dl-bar"><div class="dl-bar-fill" id="dlbar-' + id + '" style="width:0%"></div></div>' +
    '<div class="dl-status" id="dlst-' + id + '">Starting...</div>';
  q.prepend(div);
}

function pollDownload(id) {
  fetch('/download-status?id=' + id)
    .then(r => r.json())
    .then(data => {
      const el = document.getElementById('dl-' + id);
      const bar = document.getElementById('dlbar-' + id);
      const st = document.getElementById('dlst-' + id);
      if (!el) return;

      if (data.status === 'done') {
        el.className = 'dl-item done';
        bar.style.width = '100%';
        st.textContent = 'Saved to USB: ' + data.filename;
        loadUsbFiles();
        return;
      }
      if (data.status === 'error') {
        el.className = 'dl-item error';
        st.textContent = 'Error: ' + data.error;
        return;
      }

      bar.style.width = (data.progress || 0) + '%';
      st.textContent = data.message || 'Downloading...';
      setTimeout(() => pollDownload(id), 1000);
    })
    .catch(() => setTimeout(() => pollDownload(id), 2000));
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// Load USB file list
function loadUsbFiles() {
  fetch('/usb-files')
    .then(r => r.json())
    .then(data => {
      const list = document.getElementById('usbFileList');
      if (data.files.length === 0) {
        list.innerHTML = '<div class="empty-state">No audio files on USB</div>';
        return;
      }
      list.innerHTML = data.files.map(f =>
        '<div class="usb-file-item">' + escapeHtml(f) + '</div>'
      ).join('');
    });
}

// ==================== RENAME ALL ====================
function renameAll() {
  if (!confirm('Rename all MP3 files to 100, 101, 102...?')) return;
  fetch('/rename-all', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        alert('Renamed ' + data.renamed + ' file(s)!');
        loadUsbFiles();
      } else {
        alert('Error: ' + data.error);
      }
    });
}

// ==================== HISTORY ====================
function toggleHistory() {
  var list = document.getElementById('historyList');
  if (list.classList.contains('open')) {
    list.classList.remove('open');
  } else {
    loadHistory();
    list.classList.add('open');
  }
}

function loadHistory() {
  fetch('/download-history')
    .then(r => r.json())
    .then(data => {
      var list = document.getElementById('historyList');
      if (!data.history || data.history.length === 0) {
        list.innerHTML = '<div class="history-empty">No downloads yet</div>';
        return;
      }
      list.innerHTML = data.history.map(function(item, i) {
        return '<div class="history-item"><span class="history-idx">' + (i + 1) + '.</span>' + escapeHtml(item) + '</div>';
      }).join('');
    });
}

// ==================== PLAYER ====================
const audio = document.getElementById('audio');
const trackNum = document.getElementById('trackNum');
const trackName = document.getElementById('trackName');
const progressBar = document.getElementById('progressBar');
const progressFill = document.getElementById('progressFill');
const curTime = document.getElementById('curTime');
const totalTime = document.getElementById('totalTime');
const playlistEl = document.getElementById('playlist');
const btnPlay = document.getElementById('btnPlay');
const btnPrev = document.getElementById('btnPrev');
const btnNext = document.getElementById('btnNext');
const btnLoop = document.getElementById('btnLoop');
const volumeSlider = document.getElementById('volume');
const volVal = document.getElementById('volVal');
const loopLabel = document.getElementById('loopLabel');

let tracks = [];
let currentIndex = 0;
let loopMode = 'all';

function loadUsbAudio() {
  fetch('/usb-files')
    .then(r => r.json())
    .then(data => {
      const mp3s = data.files.filter(f => f.toLowerCase().endsWith('.mp3') || f.toLowerCase().endsWith('.wav'));
      tracks = mp3s.map(f => ({ name: f, url: '/usb-audio/' + encodeURIComponent(f) }));
      if (tracks.length === 0) {
        trackNum.textContent = 'NO MP3 FILES';
        trackName.textContent = 'Download some songs first';
        return;
      }
      currentIndex = 0;
      loadTrack(0);
      renderPlaylist();
    });
}

function renderPlaylist() {
  playlistEl.innerHTML = '';
  tracks.forEach((t, i) => {
    const div = document.createElement('div');
    div.className = 'playlist-item' + (i === currentIndex ? ' active' : '');
    div.innerHTML = '<span class="idx">' + (i + 1) + '</span><span class="name">' + escapeHtml(t.name) + '</span>';
    div.onclick = () => { currentIndex = i; loadTrack(i); audio.play(); updatePlayBtn(true); };
    playlistEl.appendChild(div);
  });
}

function loadTrack(i) {
  const track = tracks[i];
  audio.src = track.url;
  trackNum.textContent = 'TRACK ' + (i + 1) + ' / ' + tracks.length;
  trackName.textContent = track.name.replace(/\.[^.]+$/, '');
  renderPlaylist();
}

function formatTime(s) {
  if (isNaN(s)) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m + ':' + String(sec).padStart(2, '0');
}

function updatePlayBtn(playing) {
  btnPlay.innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
}

btnPlay.onclick = () => {
  if (tracks.length === 0) return;
  if (audio.paused) { audio.play(); updatePlayBtn(true); }
  else { audio.pause(); updatePlayBtn(false); }
};

btnNext.onclick = () => {
  if (tracks.length === 0) return;
  currentIndex = (currentIndex + 1) % tracks.length;
  loadTrack(currentIndex);
  audio.play(); updatePlayBtn(true);
};

btnPrev.onclick = () => {
  if (tracks.length === 0) return;
  if (audio.currentTime > 3) { audio.currentTime = 0; }
  else {
    currentIndex = (currentIndex - 1 + tracks.length) % tracks.length;
    loadTrack(currentIndex);
  }
  audio.play(); updatePlayBtn(true);
};

btnLoop.onclick = () => {
  if (loopMode === 'all') { loopMode = 'one'; btnLoop.classList.add('active'); }
  else if (loopMode === 'one') { loopMode = 'none'; btnLoop.classList.remove('active'); }
  else { loopMode = 'all'; }
  loopLabel.textContent = 'Loop: ' + loopMode.charAt(0).toUpperCase() + loopMode.slice(1);
};

volumeSlider.oninput = () => {
  audio.volume = volumeSlider.value / 100;
  volVal.textContent = volumeSlider.value + '%';
};
audio.volume = 0.8;

audio.ontimeupdate = () => {
  if (audio.duration) {
    progressFill.style.width = (audio.currentTime / audio.duration * 100) + '%';
    curTime.textContent = formatTime(audio.currentTime);
    totalTime.textContent = formatTime(audio.duration);
  }
};

progressBar.onclick = (e) => {
  if (audio.duration) {
    const rect = progressBar.getBoundingClientRect();
    audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
  }
};

audio.onended = () => {
  if (loopMode === 'one') { audio.currentTime = 0; audio.play(); }
  else if (loopMode === 'all') {
    currentIndex = (currentIndex + 1) % tracks.length;
    loadTrack(currentIndex); audio.play();
  } else {
    if (currentIndex < tracks.length - 1) {
      currentIndex++; loadTrack(currentIndex); audio.play();
    } else { updatePlayBtn(false); }
  }
};

document.onkeydown = (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.code === 'Space') { e.preventDefault(); btnPlay.click(); }
  if (e.code === 'ArrowRight') btnNext.click();
  if (e.code === 'ArrowLeft') btnPrev.click();
};

// ==================== FOLDER PICKER ====================
let currentDrive = '';

function loadCurrentFolder() {
  fetch('/drives')
    .then(r => r.json())
    .then(data => {
      currentDrive = data.current;
      document.getElementById('currentFolder').textContent = data.current;
      document.getElementById('filesHeader').textContent = 'Files on ' + data.current;
    });
}

function pickFolder() {
  fetch('/pick-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}'
  })
  .then(r => r.json())
  .then(data => {
    if (data.ok) {
      currentDrive = data.path;
      document.getElementById('currentFolder').textContent = data.path;
      document.getElementById('filesHeader').textContent = 'Files on ' + data.path;
      loadUsbFiles();
      loadUsbAudio();
    }
  });
}

// Auto-load on start
loadCurrentFolder();
loadUsbFiles();
</script>

</body>
</html>"""


def get_playlist_videos(url):
    """Extract video list from a YouTube playlist URL."""
    if not YTDLP_PATH:
        print("[ERROR] yt-dlp not found. Install with: winget install yt-dlp.yt-dlp")
        return None
    cmd = [YTDLP_PATH, '--flat-playlist', '-J', url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            entries = data.get('entries', [])
            videos = []
            for entry in entries:
                video_id = entry.get('id', '')
                video_url = entry.get('url', '')
                if video_id and not video_url.startswith('http'):
                    video_url = 'https://www.youtube.com/watch?v=' + video_id
                title = entry.get('title', 'Unknown')
                videos.append({'url': video_url, 'title': title})
            return videos
    except Exception as e:
        print(f"[ERROR] Playlist extraction: {e}")
    return None


def do_playlist_download(download_entries):
    """Download playlist videos sequentially."""
    for did, url in download_entries:
        do_download(did, url)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logs

    def handle_one_request(self):
        """Override to suppress ConnectionAbortedError from client disconnects."""
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Client disconnected mid-response, nothing to do

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())

        elif self.path == '/usb-files':
            files = []
            try:
                if os.path.isdir(USB_DRIVE):
                    for f in os.listdir(USB_DRIVE):
                        if f.startswith('.') or f.startswith('$'):
                            continue
                        if f.lower() == 'system volume information':
                            continue
                        full = os.path.join(USB_DRIVE, f)
                        if os.path.isfile(full):
                            files.append(f)
            except Exception as e:
                print(f"[ERROR] Cannot read {USB_DRIVE}: {e}")
                files = []
            files.sort(key=lambda x: x.lower())
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'files': files}).encode())

        elif self.path.startswith('/usb-audio/'):
            fname = urllib.parse.unquote(self.path[len('/usb-audio/'):])
            # Sanitize: only allow simple filenames, no path traversal
            fname = os.path.basename(fname)
            fpath = os.path.join(USB_DRIVE, fname)
            if os.path.isfile(fpath):
                self.send_response(200)
                if fname.lower().endswith('.mp3'):
                    self.send_header('Content-Type', 'audio/mpeg')
                elif fname.lower().endswith('.wav'):
                    self.send_header('Content-Type', 'audio/wav')
                else:
                    self.send_header('Content-Type', 'application/octet-stream')
                size = os.path.getsize(fpath)
                self.send_header('Content-Length', str(size))
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                try:
                    with open(fpath, 'rb') as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass  # Client disconnected mid-stream
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path.startswith('/browse'):
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            browse_path = params.get('path', [''])[0]
            if not browse_path or not os.path.isdir(browse_path):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid path'}).encode())
                return
            folders = []
            try:
                for item in sorted(os.listdir(browse_path), key=str.lower):
                    if item.startswith('.') or item.startswith('$'):
                        continue
                    if item.lower() == 'system volume information':
                        continue
                    full = os.path.join(browse_path, item)
                    if os.path.isdir(full):
                        folders.append(item)
            except PermissionError:
                pass
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'path': browse_path, 'folders': folders}).encode())

        elif self.path == '/drives':
            drives = get_drives()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'drives': drives, 'current': USB_DRIVE}).encode())

        elif self.path.startswith('/download-status'):
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            did = params.get('id', [''])[0]
            with download_lock:
                info = downloads.get(did, {'status': 'error', 'error': 'Not found'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(info).encode())

        elif self.path == '/download-history':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'history': download_history}).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global USB_DRIVE
        if self.path == '/pick-folder':
            folder = pick_folder()
            if folder:
                USB_DRIVE = folder
                if not USB_DRIVE.endswith(os.sep):
                    USB_DRIVE += os.sep
                print(f"[FOLDER] Selected: {USB_DRIVE}")
                self.send_json({'ok': True, 'path': USB_DRIVE})
            else:
                self.send_json({'ok': False, 'error': 'No folder selected'})
            return

        elif self.path == '/set-drive':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            new_path = body.get('path', '').strip()
            # Validate: must be a real, accessible directory
            try:
                if os.path.isdir(new_path):
                    os.listdir(new_path)  # verify we can actually read it
                    USB_DRIVE = new_path
                    if not USB_DRIVE.endswith(os.sep):
                        USB_DRIVE += os.sep
                    print(f"[DRIVE] Switched to: {USB_DRIVE}")
                    self.send_json({'ok': True, 'path': USB_DRIVE})
                else:
                    self.send_json({'ok': False, 'error': 'Folder not found: ' + new_path})
            except OSError as e:
                print(f"[ERROR] Cannot access {new_path}: {e}")
                self.send_json({'ok': False, 'error': f'Cannot access: {new_path}'})
            return

        elif self.path == '/create-folder':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            parent = body.get('parent', '').strip()
            name = body.get('name', '').strip()
            if not parent or not name or not os.path.isdir(parent):
                self.send_json({'ok': False, 'error': 'Invalid parent or name'})
                return
            # Sanitize folder name
            name = re.sub(r'[<>:"/\\|?*]', '', name).strip()
            if not name:
                self.send_json({'ok': False, 'error': 'Invalid folder name'})
                return
            new_dir = os.path.join(parent, name)
            try:
                os.makedirs(new_dir, exist_ok=True)
                self.send_json({'ok': True, 'path': new_dir})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)})
            return

        elif self.path == '/rename-all':
            # Rename all MP3s to 100, 101, 102, etc.
            try:
                mp3s = [f for f in os.listdir(USB_DRIVE) if f.lower().endswith('.mp3') and os.path.isfile(os.path.join(USB_DRIVE, f))]
                # Skip files already named as numbers
                already_named = {}
                to_rename = []
                for f in mp3s:
                    m = re.match(r'^(\d+)\.mp3$', f)
                    if m:
                        already_named[int(m.group(1))] = f
                    else:
                        to_rename.append(f)
                # Sort files to rename by modified time (oldest first)
                to_rename.sort(key=lambda f: os.path.getmtime(os.path.join(USB_DRIVE, f)))
                renamed = 0
                n = 100
                for f in to_rename:
                    while n in already_named:
                        n += 1
                    new_name = f"{n}.mp3"
                    os.rename(os.path.join(USB_DRIVE, f), os.path.join(USB_DRIVE, new_name))
                    print(f"[RENAME] {f} -> {new_name}")
                    already_named[n] = new_name
                    renamed += 1
                    n += 1
                self.send_json({'ok': True, 'renamed': renamed})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)})
            return

        elif self.path == '/download':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            url = body.get('url', '').strip()

            if not url:
                self.send_json({'error': 'No URL provided'})
                return

            global download_id

            # Check if this is a playlist URL
            is_playlist = 'list=' in url

            if is_playlist:
                videos = get_playlist_videos(url)
                if not videos:
                    self.send_json({'error': 'Could not extract playlist. Check the URL.'})
                    return

                entries = []
                download_pairs = []
                with download_lock:
                    for v in videos:
                        download_id += 1
                        did = str(download_id)
                        downloads[did] = {'status': 'queued', 'progress': 0, 'message': 'Queued...'}
                        entries.append({'id': did, 'title': v['title']})
                        download_pairs.append((did, v['url']))

                thread = threading.Thread(target=do_playlist_download, args=(download_pairs,), daemon=True)
                thread.start()

                self.send_json({'playlist': True, 'entries': entries})
            else:
                with download_lock:
                    download_id += 1
                    did = str(download_id)
                    downloads[did] = {'status': 'starting', 'progress': 0, 'message': 'Starting...'}

                thread = threading.Thread(target=do_download, args=(did, url), daemon=True)
                thread.start()

                self.send_json({'id': did, 'title': url})
        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, obj):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())


def next_song_name(target_dir):
    """Find the next available number starting from 100, skipping numbers already taken."""
    existing = set()
    for f in os.listdir(target_dir):
        m = re.match(r'^(\d+)\.mp3$', f)
        if m:
            existing.add(int(m.group(1)))
    n = 100
    while n in existing:
        n += 1
    return n, f"{n}.mp3"


def do_download(did, url):
    try:
        if not YTDLP_PATH:
            with download_lock:
                downloads[did] = {'status': 'error', 'error': 'yt-dlp not installed. Run: winget install yt-dlp.yt-dlp'}
            print("[ERROR] yt-dlp not found. Install with: winget install yt-dlp.yt-dlp")
            return

        with download_lock:
            downloads[did] = {'status': 'downloading', 'progress': 5, 'message': 'Getting video info...'}

        # Get the real video title (supports Amharic/unicode) via JSON
        video_title = ''
        try:
            title_cmd = [YTDLP_PATH, '--no-playlist', '--skip-download', '-j', url]
            title_result = subprocess.run(title_cmd, capture_output=True, timeout=30)
            if title_result.returncode == 0:
                raw = title_result.stdout.decode('utf-8', errors='replace')
                info = json.loads(raw)
                video_title = info.get('title', '')
                print(f"[TITLE] {video_title}")
            else:
                err = title_result.stderr.decode('utf-8', errors='replace')
                print(f"[TITLE FAIL] exit {title_result.returncode}: {err[:200]}")
        except Exception as e:
            print(f"[TITLE ERROR] {e}")

        # Download to a temp folder first, then move with a unique name
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix='ytdl_')

        # Build yt-dlp command: download as MP3, 192kbps
        cmd = [
            YTDLP_PATH,
            '--no-playlist',
            '-x', '--audio-format', 'mp3',
            '--audio-quality', '192K',
            '-o', os.path.join(temp_dir, 'download.%(ext)s'),
            '--no-mtime',
            '--progress',
            '--newline',
        ]

        if FFMPEG_PATH:
            ffdir = os.path.dirname(FFMPEG_PATH)
            cmd.extend(['--ffmpeg-location', ffdir])

        cmd.append(url)

        # Ensure the target directory exists and is accessible
        target_dir = USB_DRIVE
        if not os.path.isdir(os.path.splitdrive(target_dir)[0] + os.sep):
            with download_lock:
                downloads[did] = {'status': 'error', 'error': f'Drive not available: {target_dir}'}
            print(f"[ERROR] Drive not available: {target_dir}")
            return
        os.makedirs(target_dir, exist_ok=True)
        print(f"[DEBUG] Saving to: {target_dir}")
        print(f"[DEBUG] Full command: {' '.join(cmd)}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',
                                cwd=target_dir)

        filename = ''
        for line in proc.stdout:
            line = line.strip()
            if line:
                print(f"[yt-dlp] {line}")

            # Parse video title from yt-dlp output as fallback
            if not video_title:
                title_match = re.search(r'\[download\]\s+Downloading\s+video\s+\d+\s+of\s+\d+', line)
                if not title_match:
                    title_match2 = re.search(r'\[youtube\]\s+\S+:\s+Downloading\s+webpage', line)

            # Parse progress
            pct_match = re.search(r'(\d+(?:\.\d+)?)%', line)
            if pct_match:
                pct = float(pct_match.group(1))
                with download_lock:
                    downloads[did]['progress'] = min(pct, 99)
                    downloads[did]['message'] = f'Downloading... {pct:.0f}%'

            # Parse destination filename
            dest_match = re.search(r'\[(?:ExtractAudio|Merger)\].*?Destination:\s*(.+)', line)
            if dest_match:
                filename = os.path.basename(dest_match.group(1).strip())

            if not dest_match:
                dest_match2 = re.search(r'Destination:\s*(.+)', line)
                if dest_match2:
                    filename = os.path.basename(dest_match2.group(1).strip())

            # Detect already downloaded
            already = re.search(r'has already been downloaded', line)
            if already:
                with download_lock:
                    downloads[did]['progress'] = 100
                    downloads[did]['message'] = 'Already downloaded'

        proc.wait()

        if proc.returncode == 0:
            # Find the mp3 in the temp folder
            temp_mp3s = [f for f in os.listdir(temp_dir) if f.lower().endswith('.mp3')]
            if not temp_mp3s:
                # Fallback: grab any file
                temp_mp3s = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]

            if temp_mp3s:
                src_file = os.path.join(temp_dir, temp_mp3s[0])
                # Re-check USB_DRIVE is accessible (could have been unplugged during download)
                if not os.path.isdir(os.path.splitdrive(USB_DRIVE)[0] + os.sep):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    with download_lock:
                        downloads[did] = {'status': 'error', 'error': f'Drive not available: {USB_DRIVE}'}
                    print(f"[ERROR] Drive unavailable after download: {USB_DRIVE}")
                    return
                os.makedirs(USB_DRIVE, exist_ok=True)

                # Use the Amharic title as the filename
                if video_title:
                    # Remove characters not allowed in Windows filenames
                    safe_title = re.sub(r'[<>:"/\\|?*]', '', video_title).strip()
                    if not safe_title:
                        safe_title = 'download'
                    dest_name = safe_title + '.mp3'
                    dest_file = os.path.join(USB_DRIVE, dest_name)
                    # If file with same name exists, add a number
                    counter = 2
                    while os.path.isfile(dest_file):
                        dest_name = f"{safe_title} ({counter}).mp3"
                        dest_file = os.path.join(USB_DRIVE, dest_name)
                        counter += 1
                else:
                    # Fallback to numbered name
                    _, dest_name = next_song_name(USB_DRIVE)
                    dest_file = os.path.join(USB_DRIVE, dest_name)

                shutil.move(src_file, dest_file)
                filename = dest_name
                print(f"[OK] Saved as: {dest_file}")
            else:
                filename = 'unknown'
                print(f"[WARN] No mp3 found in temp folder")

            # Clean up temp folder
            shutil.rmtree(temp_dir, ignore_errors=True)

            with download_lock:
                downloads[did] = {'status': 'done', 'progress': 100, 'filename': filename}
            # Save to download history
            title = filename.rsplit('.', 1)[0]
            download_history.append(title)
            save_history()
            print(f"[OK] Downloaded: {filename}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            with download_lock:
                downloads[did] = {'status': 'error', 'error': 'yt-dlp exited with code ' + str(proc.returncode)}
            print(f"[FAIL] yt-dlp exit code {proc.returncode}")

    except Exception as e:
        with download_lock:
            downloads[did] = {'status': 'error', 'error': str(e)}
        print(f"[ERROR] {e}")


class QuietHTTPServer(http.server.HTTPServer):
    """HTTPServer that suppresses connection abort tracebacks."""
    def handle_error(self, request, client_address):
        import traceback
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            pass  # Silently ignore client disconnects
        else:
            traceback.print_exc()


if __name__ == '__main__':
    server = QuietHTTPServer(('127.0.0.1', PORT), Handler)
    print(f"\n  USB Player + YouTube Downloader")
    print(f"  Open http://localhost:{PORT} in your browser\n")
    webbrowser.open(f'http://localhost:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
