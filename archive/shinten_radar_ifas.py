#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新店レーダー / shinten_radar.py

厚労省「食品衛生申請等システム」等の営業許可オープンデータCSVから
「まだ取材していない新規開店店舗」を抽出する。

  シモキタVer : 下北沢エリアに絞り、鮮度重視で全件出す
  東京Ver     : 都内全域から、チェーン除外＋エリア重み付けでスコア上位を出す

使い方:
  1) まず列名を確認
     python3 shinten_radar.py --inspect data/setagaya.csv

  2) シモキタVer
     python3 shinten_radar.py --mode shimokita --input data/*.csv --months 3

  3) 東京Ver
     python3 shinten_radar.py --mode tokyo --input data/*.csv --months 1 --top 30

  4) 取材済みを除外
     python3 shinten_radar.py --mode shimokita --input data/*.csv --known covered.csv

出典表示（CC BY）が必要です。README参照。
"""

import argparse
import glob
import os
import re
import sys
from datetime import datetime, timedelta

import pandas as pd

# ---------------------------------------------------------------- 設定

# 列名の候補。自治体ごとに揺れるので候補で拾う。
COLUMN_CANDIDATES = {
    "name": ["屋号", "屋号又は名称", "施設名称", "施設名", "営業所名称", "名称", "店舗名"],
    "address": ["施設所在地", "営業所所在地", "所在地", "住所", "施設住所", "営業施設所在地"],
    "category": ["業種名", "業種", "営業の種類", "業種等", "許可業種", "業種区分"],
    "date": ["許可年月日", "許可日", "届出年月日", "許可・届出年月日", "申請年月日", "許可開始日"],
    "city": ["自治体名", "保健所設置自治体", "都道府県名", "自治体", "保健所名"],
}

# シモキタVer の対象エリア（住所文字列の部分一致）
SHIMOKITA_AREAS = ["北沢", "代沢", "代田"]

# 東京Ver のエリア重み。取材実績・話題化しやすさで加点。
AREA_WEIGHTS = {
    "恵比寿": 4, "中目黒": 4, "上目黒": 3, "三軒茶屋": 3,
    "北沢": 4, "代沢": 3, "代田": 3,
    "吉祥寺": 3, "新宿": 2, "神楽坂": 3, "代官山": 3,
    "自由が丘": 3, "学芸大学": 2, "祐天寺": 2, "西荻窪": 2,
    "高円寺": 2, "中野": 2, "渋谷": 2, "原宿": 2, "神宮前": 2,
    "浅草": 2, "蔵前": 2, "清澄": 2, "門前仲町": 2,
}

# 飲食店として扱う業種（部分一致）
EATERY_CATEGORIES = ["飲食店", "喫茶店"]

# 原則除外する業種（自宅営業・製造業が混ざりやすい）
EXCLUDE_CATEGORIES = [
    "菓子製造", "そうざい製造", "食肉販売", "魚介類販売", "乳類販売",
    "食料品等販売", "アイスクリーム類製造", "コップ式",
    "自動販売機", "行商", "集乳", "乳処理",
]

# チェーン判定：同一屋号がこの件数以上あればチェーンとみなす
CHAIN_THRESHOLD = 3

# 明らかなチェーンの手動除外（部分一致）
CHAIN_KEYWORDS = [
    "セブンイレブン", "ファミリーマート", "ローソン", "ミニストップ",
    "マクドナルド", "スターバックス", "ドトール", "サイゼリヤ", "ガスト",
    "吉野家", "松屋", "すき家", "餃子の王将", "日高屋", "コメダ",
    "串カツ田中", "鳥貴族",
]


# ---------------------------------------------------------------- 読み込み

def read_csv_any(path):
    """自治体CSVは文字コードがバラバラなので順に試す。"""
    last_err = None
    for enc in ("cp932", "utf-8-sig", "utf-8", "euc_jp"):
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str, on_bad_lines="skip")
            df["_source_file"] = os.path.basename(path)
            return df
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"読み込み失敗: {path} ({last_err})")


def detect_columns(df):
    """列名を候補から推測する。見つからない項目は None。"""
    found = {}
    cols = list(df.columns)
    for key, cands in COLUMN_CANDIDATES.items():
        hit = None
        # 完全一致優先
        for c in cands:
            if c in cols:
                hit = c
                break
        # 部分一致
        if hit is None:
            for c in cols:
                if any(cand in str(c) for cand in cands):
                    hit = c
                    break
        found[key] = hit
    return found


# ---------------------------------------------------------------- 日付

WAREKI = {"令和": 2018, "平成": 1988, "R": 2018, "H": 1988}


def parse_jp_date(s):
    """2026/4/1, 2026-04-01, 令和8年4月1日, R8.4.1 などを吸収する。"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return pd.NaT
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return pd.NaT

    for era, base in WAREKI.items():
        if s.startswith(era):
            body = s[len(era):]
            nums = re.findall(r"\d+", body.replace("元", "1"))
            if len(nums) >= 3:
                y = base + int(nums[0])
                try:
                    return pd.Timestamp(y, int(nums[1]), int(nums[2]))
                except ValueError:
                    return pd.NaT
            return pd.NaT

    try:
        return pd.to_datetime(s, errors="coerce")
    except Exception:  # noqa: BLE001
        return pd.NaT


# ---------------------------------------------------------------- 整形

def normalize(df, colmap):
    out = pd.DataFrame()
    out["屋号"] = df[colmap["name"]].fillna("").astype(str).str.strip() if colmap["name"] else ""
    out["住所"] = df[colmap["address"]].fillna("").astype(str).str.strip() if colmap["address"] else ""
    out["業種"] = df[colmap["category"]].fillna("").astype(str).str.strip() if colmap["category"] else ""
    out["自治体"] = df[colmap["city"]].fillna("").astype(str).str.strip() if colmap["city"] else ""
    if colmap["date"]:
        out["許可日"] = df[colmap["date"]].map(parse_jp_date)
    else:
        out["許可日"] = pd.NaT
    out["_source_file"] = df.get("_source_file", "")
    return out


def is_eatery(cat):
    return any(k in cat for k in EATERY_CATEGORIES)


def is_excluded(cat):
    return any(k in cat for k in EXCLUDE_CATEGORIES)


def looks_like_chain(name, name_counts):
    if not name:
        return False
    if any(k in name for k in CHAIN_KEYWORDS):
        return True
    return name_counts.get(name, 0) >= CHAIN_THRESHOLD


def area_of(address):
    for area in AREA_WEIGHTS:
        if area in address:
            return area
    return ""


# ---------------------------------------------------------------- 抽出

def build(df, mode, months, known_names, include_manufacturing):
    df = df.copy()

    # 期間で絞る
    if months and df["許可日"].notna().any():
        cutoff = pd.Timestamp(datetime.now()) - timedelta(days=int(months) * 31)
        df = df[df["許可日"].isna() | (df["許可日"] >= cutoff)]

    # 業種
    if not include_manufacturing:
        df = df[~df["業種"].map(is_excluded)]
    df = df[df["業種"].map(is_eatery) | (df["業種"] == "")]

    # プライバシー：屋号なし＝個人名営業の可能性が高いので落とす
    df = df[df["屋号"].str.len() > 0]

    # チェーン判定
    name_counts = df["屋号"].value_counts().to_dict()
    df["チェーン推定"] = df["屋号"].map(lambda n: looks_like_chain(n, name_counts))

    # 取材済み除外
    if known_names:
        df = df[~df["屋号"].isin(known_names)]

    if mode == "shimokita":
        df = df[df["住所"].apply(lambda a: any(x in a for x in SHIMOKITA_AREAS))]
        df = df[~df["チェーン推定"]]
        df["エリア"] = df["住所"].map(area_of)
        df["スコア"] = ""
        df = df.sort_values("許可日", ascending=False)
    else:
        df["エリア"] = df["住所"].map(area_of)
        df["スコア"] = df.apply(lambda r: score_row(r), axis=1)
        df = df[df["スコア"] > 0]
        df = df.sort_values(["スコア", "許可日"], ascending=[False, False])

    return df.drop_duplicates(subset=["屋号", "住所"])


def score_row(r):
    s = 0
    if r["チェーン推定"]:
        return 0
    s += AREA_WEIGHTS.get(r["エリア"], 0)
    if is_eatery(r["業種"]):
        s += 2
    if pd.notna(r["許可日"]):
        days = (pd.Timestamp(datetime.now()) - r["許可日"]).days
        if days <= 31:
            s += 3
        elif days <= 62:
            s += 1
    # 屋号が短すぎる／記号だけ＝情報量が薄いので減点
    if len(str(r["屋号"])) <= 1:
        s -= 2
    return s


# ---------------------------------------------------------------- 出力

def to_markdown(df, mode, top):
    label = "シモキタメディア" if mode == "shimokita" else "東京グルメディア"
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 新店レーダー / {label} 版　({today} 抽出)", ""]
    if df.empty:
        lines.append("該当なし。期間を広げるか、対象自治体のファイルを追加してください。")
        return "\n".join(lines)

    lines.append(f"候補 {len(df)} 件（表示 {min(top, len(df))} 件）")
    lines.append("")
    for i, (_, r) in enumerate(df.head(top).iterrows(), 1):
        d = r["許可日"].strftime("%Y-%m-%d") if pd.notna(r["許可日"]) else "不明"
        head = f"{i}. **{r['屋号']}**"
        if mode == "tokyo" and r["スコア"] != "":
            head += f"　[score {int(r['スコア'])}]"
        lines.append(head)
        lines.append(f"   - 住所　{r['住所']}")
        lines.append(f"   - 業種　{r['業種']}　/　許可日　{d}")
        lines.append(f"   - 確認　食べログ・Instagramで「{r['屋号']}」を検索して実在とジャンルを裏取り")
        lines.append("")
    lines.append("---")
    lines.append('出典：「食品衛生申請等システム」（厚生労働省）ほか各自治体オープンデータ（CC BY 4.0）')
    lines.append("※許可データに業態（ラーメン／居酒屋等）は含まれません。ジャンルは必ず裏取りしてください。")
    lines.append("※廃業情報は反映されません。許可済み＝営業中とは限りません。")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="新店レーダー")
    p.add_argument("--input", nargs="*", default=[], help="CSVファイル（glob可）")
    p.add_argument("--inspect", help="列名と先頭数行を表示して終了")
    p.add_argument("--mode", choices=["shimokita", "tokyo"], default="shimokita")
    p.add_argument("--months", type=int, default=3, help="直近Nヶ月の許可のみ（0で無制限）")
    p.add_argument("--top", type=int, default=30, help="Markdownに出す件数")
    p.add_argument("--known", help="取材済み店舗リストCSV（屋号 列）")
    p.add_argument("--out", default="out", help="出力ディレクトリ")
    p.add_argument("--include-manufacturing", action="store_true",
                   help="菓子製造等も含める（既定は除外）")
    args = p.parse_args()

    if args.inspect:
        df = read_csv_any(args.inspect)
        print(f"行数: {len(df)}")
        print("\n--- 列名 ---")
        for c in df.columns:
            print(f"  {c}")
        print("\n--- 推測した対応 ---")
        for k, v in detect_columns(df).items():
            print(f"  {k:9s} -> {v}")
        print("\n--- 先頭3行 ---")
        print(df.head(3).to_string())
        return

    paths = []
    for pat in args.input:
        paths.extend(sorted(glob.glob(pat)))
    if not paths:
        print("入力CSVがありません。--input data/*.csv のように指定してください。", file=sys.stderr)
        sys.exit(1)

    frames = []
    for path in paths:
        raw = read_csv_any(path)
        colmap = detect_columns(raw)
        missing = [k for k in ("name", "address") if colmap[k] is None]
        if missing:
            print(f"[警告] {os.path.basename(path)}: {missing} の列が特定できません。"
                  f" --inspect で列名を確認してください。", file=sys.stderr)
        frames.append(normalize(raw, colmap))
    df = pd.concat(frames, ignore_index=True)
    print(f"読み込み {len(df)} 行 / {len(paths)} ファイル")

    known = set()
    if args.known and os.path.exists(args.known):
        kdf = read_csv_any(args.known)
        col = "屋号" if "屋号" in kdf.columns else kdf.columns[0]
        known = set(kdf[col].fillna("").astype(str).str.strip())
        print(f"取材済み {len(known)} 件を除外")

    res = build(df, args.mode, args.months, known, args.include_manufacturing)
    print(f"抽出 {len(res)} 件 (mode={args.mode})")

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(args.out, f"shinten_{args.mode}_{stamp}.csv")
    md_path = os.path.join(args.out, f"shinten_{args.mode}_{stamp}.md")

    cols = ["屋号", "住所", "業種", "許可日", "エリア", "スコア", "自治体", "_source_file"]
    res.reindex(columns=cols).to_csv(csv_path, index=False, encoding="utf-8-sig")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(res, args.mode, args.top))

    print(f"出力: {csv_path}")
    print(f"出力: {md_path}")


if __name__ == "__main__":
    main()
