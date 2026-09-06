#!/usr/bin/env python3
"""shinten_radar - 厚労省 食品衛生申請等システム(IFAS)の営業許可オープンデータを読む。

現状の実装範囲は Task 1、すなわち CSV の列解決と `--inspect` のみ。
スコアリング・新店抽出のチューニングは未実装（Task 2）。

  python shinten_radar.py --inspect data/13113_food_business_all.csv
  python shinten_radar.py --inspect data/            # ディレクトリ内の全CSV

列名は 2026年07月末版の実データ（東京都エリア26ファイル / 63,198行）で確認済み。
詳細は OPENDATA_NOTES.md を参照。
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# 列名候補
#
# 先頭が 2026年07月末版の実データで確認した実名。以降は表記ゆれの保険。
# IFAS の全26ファイルはヘッダが完全に同一だったため、実質は先頭だけで解決する。
# --------------------------------------------------------------------------

COLUMN_CANDIDATES: dict[str, list[str]] = {
    "muni_code":         ["自治体コード"],
    "row_id":            ["行番号"],
    "pref":              ["都道府県名", "都道府県"],
    # 注意: これは「管轄自治体の所在地」であって施設住所ではない。施設住所は address。
    "juris_muni":        ["市区町村名", "市区町村"],
    "name":              ["営業施設名称、屋号又は商号", "営業施設名称", "屋号又は商号", "施設名称"],
    "name_kana":         ["営業施設名称、屋号又は商号（フリガナ）", "営業施設名称（フリガナ）", "フリガナ"],
    "business_type":     ["営業の種類", "業種"],
    "category":          ["業態"],
    "address":           ["営業施設所在地", "施設所在地", "所在地"],
    "address_detail":    ["営業施設方書", "方書"],
    "lat":               ["緯度"],
    "lon":               ["経度"],
    "phone":             ["営業施設電話番号", "電話番号"],
    "corp_name":         ["法人名"],
    "corp_number":       ["法人番号"],
    "corp_address":      ["法人住所"],
    "permit_no":         ["許可番号"],
    "first_permit_date": ["初回許可年月日"],
    "permit_date":       ["許可年月日"],
    "permit_start":      ["許可開始日"],
    "permit_end":        ["許可満了日"],
    "closed_date":       ["廃業年月日"],
    "app_type":          ["申請区分"],
    "permit_cond":       ["許可条件"],
    "note":              ["備考"],
}

# 実データで全行空だった列。--inspect で 0% でも異常ではない。
KNOWN_EMPTY = {"note"}

# 「許可」系にしか値が入らない列。「届出」系では空が正常。
PERMIT_ONLY = {
    "permit_no", "first_permit_date", "permit_date", "permit_start", "permit_end",
}

ENCODING = "utf-8-sig"  # BOM付きUTF-8。CP932ではない。


# --------------------------------------------------------------------------
# 列解決
# --------------------------------------------------------------------------

def normalize_header(s: str) -> str:
    """ヘッダ比較用の正規化。全角/半角ゆれと空白・記号ゆれを吸収する。"""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("　", "").replace(" ", "").replace("\t", "")
    for ch in "()（）,、":
        s = s.replace(ch, "")
    return s.strip().lower()


@dataclass
class ColumnMap:
    """論理フィールド名 -> CSV の列インデックス。"""

    header: list[str]
    index: dict[str, int]
    unresolved: list[str]

    def get(self, row: list[str], field: str) -> str:
        i = self.index.get(field)
        if i is None or i >= len(row):
            return ""
        return row[i].strip()


def resolve_columns(header: list[str]) -> ColumnMap:
    lookup: dict[str, int] = {}
    for i, name in enumerate(header):
        lookup.setdefault(normalize_header(name), i)

    index: dict[str, int] = {}
    unresolved: list[str] = []
    for field, candidates in COLUMN_CANDIDATES.items():
        for cand in candidates:
            i = lookup.get(normalize_header(cand))
            if i is not None:
                index[field] = i
                break
        else:
            unresolved.append(field)
    return ColumnMap(header=header, index=index, unresolved=unresolved)


# --------------------------------------------------------------------------
# 読み込み
# --------------------------------------------------------------------------

def read_csv(path: Path) -> tuple[ColumnMap, list[list[str]]]:
    with path.open(encoding=ENCODING, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [r for r in reader]
    return resolve_columns(header), rows


def csv_paths(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.glob("*.csv"))
    return [target]


# --------------------------------------------------------------------------
# --inspect
# --------------------------------------------------------------------------

def inspect(target: Path) -> int:
    paths = csv_paths(target)
    if not paths:
        print(f"CSVが見つかりません: {target}", file=sys.stderr)
        return 1

    signatures: dict[tuple[str, ...], list[str]] = {}
    all_rows: list[list[str]] = []
    colmap: ColumnMap | None = None

    for p in paths:
        cm, rows = read_csv(p)
        signatures.setdefault(tuple(cm.header), []).append(p.name)
        all_rows.extend(rows)
        if colmap is None:
            colmap = cm

    assert colmap is not None
    total = len(all_rows)

    print(f"対象      : {target}")
    print(f"ファイル数: {len(paths)}")
    print(f"データ行数: {total:,}")
    print(f"文字コード: {ENCODING}")
    print()

    if len(signatures) > 1:
        print(f"!! ヘッダが {len(signatures)} 種類あります:")
        for sig, names in signatures.items():
            print(f"   {len(sig)}列 <- {', '.join(names[:5])}"
                  + (" ..." if len(names) > 5 else ""))
        print()

    print(f"--- CSVの実際の列名 ({len(colmap.header)}列) ---")
    for i, name in enumerate(colmap.header):
        print(f"  {i:>2}  {name}")
    print()

    print(f"--- 列の解決結果 ({len(COLUMN_CANDIDATES)}項目) ---")
    print(f"  {'field':<20}{'col':>4}  {'fill%':>7}  {'header':<34}sample")
    for field in COLUMN_CANDIDATES:
        i = colmap.index.get(field)
        if i is None:
            print(f"  {field:<20}{'--':>4}  {'':>7}  {'*** 未解決 ***':<34}")
            continue
        vals = [r[i].strip() for r in all_rows if i < len(r) and r[i].strip()]
        fill = 100 * len(vals) / total if total else 0.0
        sample = vals[0][:24] if vals else ""
        note = ""
        if not vals and field in KNOWN_EMPTY:
            note = "  (実データでも常に空)"
        elif field in PERMIT_ONLY:
            note = "  (許可系のみ)"
        print(f"  {field:<20}{i:>4}  {fill:>6.1f}%  {colmap.header[i]:<34}{sample}{note}")
    print()

    if colmap.unresolved:
        print(f"!! 未解決の項目 ({len(colmap.unresolved)}): {', '.join(colmap.unresolved)}")
        return 1

    print(f"OK: {len(COLUMN_CANDIDATES)}項目すべて解決しました。")
    return 0


# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", metavar="PATH", type=Path, required=True,
                    help="CSVファイルまたはディレクトリの列構成を検査する")
    args = ap.parse_args(argv[1:])
    return inspect(args.inspect)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
