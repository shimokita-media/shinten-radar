@echo off
REM 新店レーダー 月次実行（タスクスケジューラから毎月16日に呼ぶ）
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python shinten_radar.py --last 1 --notify >> out\run.log 2>&1
python heiten_radar.py --expired >> out\run.log 2>&1
python timeline.py >> out\run.log 2>&1
python build_map.py >> out\run.log 2>&1
