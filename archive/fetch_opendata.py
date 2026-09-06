#!/usr/bin/env python3
"""厚労省 食品衛生申請等システム(IFAS)の営業許可オープンデータを取得する。

ダウンロードURLは自治体コードだけで組み立てられ、セッション・Cookie は不要。
月次更新(前月末時点)なので、月1回まとめて叩けばよい。

  python fetch_opendata.py            # 東京23区 + 東京都 + 八王子 + 町田
  python fetch_opendata.py 13113      # 特定の自治体だけ
"""

import sys
import urllib.request
from pathlib import Path

BASE = "https://i2fas.mhlw.go.jp/faspub/page/opendatadownload.jsp?param={code}_food_business_all.csv"

# 東京都エリアは保健所設置主体ごとに26ファイルに分かれる。
# 13000 は「多摩地域(八王子・町田を除く)＋島しょ」であり 23 区を含まない点に注意。
TOKYO_23KU = [f"131{i:02d}" for i in range(1, 24)]  # 13101..13123
TOKYO_ALL = TOKYO_23KU + ["13000", "13201", "13209"]

DATA_DIR = Path(__file__).resolve().parent / "data"


def fetch(code: str, dest_dir: Path) -> Path:
    name = f"{code}_food_business_all.csv"
    dest = dest_dir / name
    with urllib.request.urlopen(BASE.format(code=code), timeout=180) as resp:
        body = resp.read()
    dest.write_bytes(body)
    return dest


def main(argv: list[str]) -> int:
    codes = argv[1:] or TOKYO_ALL
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for code in codes:
        dest = fetch(code, DATA_DIR)
        print(f"{code} -> {dest.name} ({dest.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
