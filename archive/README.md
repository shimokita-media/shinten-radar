# archive — 旧システム（厚労省 食品衛生申請等システム版）

世田谷区のCSVに切り替える前に作っていた、厚労省IFASの全国データを扱う系統。
動くが現在は使っていない。参照用に残してある。

- `shinten_radar_ifas.py` — 旧本体。shimokita / tokyo の2モードでスコアリングする設計
- `fetch_opendata.py` — IFASからのダウンロード補助
- `OPENDATA_NOTES.md` — IFASデータの実地調査メモ
- `TASKS.md` — 旧設計時のタスクリスト
- `ifas_data/` — 東京都23区＋一部市のCSV（約21MB）
- `_my_task1_draft/` — 下書き
- `shinten_shimokita_*` `shinten_tokyo_*` — 旧システムの出力サンプル

現行システムは1つ上のディレクトリ。README.md を参照。
