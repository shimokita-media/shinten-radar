#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新店レーダー / shinten_radar.py  (世田谷区版)

世田谷区が毎月15日ごろ公開する「新規許可施設一覧」CSVを取得し、
下北沢エリア（北沢・代沢・代田・大原・羽根木）の新店候補を抽出する。

使い方:
    python shinten_radar.py 8 5 8 6 8 7       # 令和8年5月・6月・7月分
    python shinten_radar.py --last 3          # 直近3ヶ月分
    python shinten_radar.py --last 1 --notify # 抽出してLINE通知（初出のみ）

出典：「新規許可施設一覧」（世田谷区）
"""

import argparse
import calendar
import csv
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from urllib.parse import quote

import requests

BASE_URL = "https://www.city.setagaya.lg.jp/documents/3246/shinkir{stem}.csv"

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "data", "shinki")
OUT_DIR = os.path.join(HERE, "out")
STATE_PATH = os.path.join(HERE, "state", "seen.json")
CHAINS_PATH = os.path.join(HERE, "chains.txt")

# 対象エリア（施設所在地の部分一致）
AREAS = ["北沢", "代沢", "代田", "大原", "羽根木"]

# AREAS に部分一致してしまうが、下北沢エリアではない町名。
# 「上北沢」は京王線沿線で下北沢とは別エリアなので除く。
NOT_AREAS = ["上北沢"]

# 令和 → 西暦
REIWA_BASE = 2018

# 読み込みを試す文字コード（実データは UTF-8 BOM付き。将来の揺れに備えて順に試す）
ENCODINGS = ["utf-8-sig", "cp932", "utf-8", "euc_jp"]

COLS = {
    "name": "施設屋号",
    "address": "施設所在地",
    "sub_address": "施設方書",
    "operator": "営業者名",
    "tel": "営業所電話番号",
    "permit_no": "許可番号",
    "category": "業種",
    "first_date": "初回許可日",
    "start_date": "許可開始日",
    "end_date": "許可満了日",
}


# ------------------------------------------------------------------ 取得

def month_stem(era_year, month):
    """令和8年7月 -> '080731'（その月の末日でファイル名が決まる）"""
    year = REIWA_BASE + era_year
    last_day = calendar.monthrange(year, month)[1]
    return "%02d%02d%02d" % (era_year, month, last_day)


def fetch_month(era_year, month, refresh=False):
    """CSVを取得してキャッシュし、ローカルパスを返す。"""
    stem = month_stem(era_year, month)
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "shinkir%s.csv" % stem)
    if os.path.exists(path) and not refresh:
        print("  キャッシュ利用: %s" % os.path.basename(path))
        return path

    url = BASE_URL.format(stem=stem)
    res = requests.get(url, timeout=30)
    if res.status_code != 200:
        raise FileNotFoundError("取得失敗 HTTP %s: %s" % (res.status_code, url))
    with open(path, "wb") as f:
        f.write(res.content)
    print("  取得: %s (%s bytes)" % (url, format(len(res.content), ",")))
    return path


def read_rows(path):
    """文字コードを順に試して DictReader の行リストを返す。"""
    raw = open(path, "rb").read()
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        rows = list(csv.DictReader(io.StringIO(text)))
        if rows and COLS["name"] in rows[0]:
            print("  文字コード: %s / %d 行" % (enc, len(rows)))
            return rows
        print("  文字コード %s では列名が一致せず、次を試します" % enc)
    raise RuntimeError("読み込み失敗（列名を特定できません）: %s" % path)


# ------------------------------------------------------------------ 整形

def parse_wareki(s):
    """'令和8年7月1日' -> date。読めなければ None。"""
    if not s:
        return None
    s = s.strip().replace("元年", "1年")
    # 全許可施設一覧の初回許可日は昭和まで遡る（現存する最古は昭和）
    m = re.search(r"(令和|平成|昭和)\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if m:
        era, y, mo, d = m.group(1), *(int(x) for x in m.groups()[1:])
        base = {"令和": REIWA_BASE, "平成": 1988, "昭和": 1925}[era]
        try:
            return date(base + y, mo, d)
        except ValueError:
            return None
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def zen2han(s):
    """全角の数字・英字を半角に。住所の '二丁目１４番８号' -> '二丁目14番8号'"""
    return unicodedata.normalize("NFKC", s or "").strip()


def unit_key(sub):
    """方書を突き合わせ用に正規化する。'芳澤ビル1階' と '芳澤ビル1F' を同じ値にする。

    同じ番地に別のビルが建っていたり、同じビルの別の階だったりするので、
    「跡地」を言うには番地だけでなく方書まで一致している必要がある。
    方書が空や '-' のときは区画を特定できないので None を返す。"""
    s = zen2han(sub).replace(" ", "").upper()
    if s in ("", "-", "ー", "―", "‐"):
        return None
    s = s.replace("地下", "B")
    s = re.sub(r"(\d+)階", r"\1F", s)
    s = re.sub(r"B(\d+)F", r"B\1", s)   # 'B1F' と 'B1' を揃える
    s = re.sub(r"(\d+)号室$", r"\1", s)
    return s


def same_unit(sub_a, sub_b):
    """同じ区画とみなせるか。どちらかの方書が無ければ判定しない（False）。"""
    a, b = unit_key(sub_a), unit_key(sub_b)
    return bool(a) and a == b


def in_area(address):
    if any(x in address for x in NOT_AREAS):
        return ""
    for a in AREAS:
        if a in address:
            return a
    return ""


def load_chains():
    if not os.path.exists(CHAINS_PATH):
        return []
    out = []
    with open(CHAINS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def is_chain(name, chains):
    """一致したチェーン名を返す（なければ空文字）。全半角の揺れを吸収する。"""
    n = zen2han(name)
    for c in chains:
        c2 = zen2han(c)
        if c2 and c2 in n:
            return c
    return ""


def normalize(raw, src, chains):
    addr = zen2han(raw.get(COLS["address"], ""))
    name = (raw.get(COLS["name"], "") or "").strip()
    start = parse_wareki(raw.get(COLS["start_date"], ""))
    first = parse_wareki(raw.get(COLS["first_date"], ""))

    # 許可開始日が月の1日 = 前許可の満了翌日 = 更新の可能性が高い
    renewal = bool(start and start.day == 1)

    chain = is_chain(name, chains)
    if renewal:
        kind = "更新"
    elif chain:
        kind = "チェーン"
    else:
        kind = "個店"

    return {
        "屋号": name,
        "住所": addr,
        "方書": zen2han(raw.get(COLS["sub_address"], "")),
        "エリア": in_area(addr),
        "業種": (raw.get(COLS["category"], "") or "").strip(),
        "許可番号": (raw.get(COLS["permit_no"], "") or "").strip(),
        "初回許可日": first.isoformat() if first else "",
        "許可開始日": start.isoformat() if start else "",
        "判定": kind,
        "チェーン名": chain,
        "出典月": src,
        # 住所だけだと建物にピンが落ちるので、店名を先頭に付けて店そのものを引かせる
        "地図": "https://www.google.com/maps/search/?api=1&query="
                + quote(zen2han(name) + " " + addr),
        "検索": "https://www.google.com/search?q=" + quote(zen2han(name) + " " + addr),
    }


def key_of(r):
    return r["屋号"] + "|" + r["住所"]


# ------------------------------------------------------------------ 状態

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# ------------------------------------------------------------------ 出力

FOOTER = [
    "---",
    "出典：「新規許可施設一覧」（世田谷区）",
    "※「更新とみられる」は許可開始日が月の1日の行。更新許可は前許可の満了翌日＝1日開始になりやすいため。",
    "　ただし新店がたまたま1日開業した場合もここに入る。判断は現地で。",
    "※業態（ラーメン／居酒屋等）はデータに存在しない。必ず自分で裏取りすること。",
    "※廃業・閉店は反映されない。許可済み＝営業中とは限らない。",
]


def render_md(groups, months_label, new_keys):
    today = datetime.now().strftime("%Y-%m-%d")
    L = ["# 新店レーダー／下北沢エリア　(%s　抽出日 %s)" % (months_label, today), ""]
    total = sum(len(v) for v in groups.values())
    L.append("対象 %d 件　個店 %d ／ チェーン %d ／ 更新とみられる %d"
             % (total, len(groups["個店"]), len(groups["チェーン"]), len(groups["更新"])))
    L.append("")

    for title, kind in [("個店の新店候補", "個店"),
                        ("チェーンの新店候補", "チェーン"),
                        ("更新とみられる", "更新")]:
        rows = groups[kind]
        L.append("## %s（%d件）" % (title, len(rows)))
        L.append("")
        if not rows:
            L.append("該当なし")
            L.append("")
            continue
        for i, r in enumerate(rows, 1):
            mark = " 🆕" if key_of(r) in new_keys else ""
            head = "%d. **%s**%s" % (i, r["屋号"], mark)
            if r["チェーン名"]:
                head += "　［%s］" % r["チェーン名"]
            L.append(head)
            L.append(("   - 住所　%s　%s" % (r["住所"], r["方書"])).rstrip())
            L.append("   - 業種　%s　/　許可開始日　%s" % (r["業種"], r["許可開始日"]))
            L.append("   - [地図](%s)　[検索](%s)" % (r["地図"], r["検索"]))
            L.append("   - 確認したこと：")
            L.append("")
    L.extend(FOOTER)
    return "\n".join(L) + "\n"


CSV_COLS = ["屋号", "住所", "方書", "エリア", "業種", "許可番号",
            "初回許可日", "許可開始日", "判定", "チェーン名", "出典月", "地図", "検索"]


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def merge_all_csv(rows):
    """out/shinten_all.csv に累積（地図生成用）。屋号＋住所で重複排除。"""
    path = os.path.join(OUT_DIR, "shinten_all.csv")
    merged = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                merged[r.get("屋号", "") + "|" + r.get("住所", "")] = r
    for r in rows:
        merged[key_of(r)] = r
    write_csv(path, list(merged.values()))
    return path


# ------------------------------------------------------------------ 通知

def load_dotenv():
    p = os.path.join(HERE, ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def notify_text(rows, months_label):
    if not rows:
        return "【新店レーダー】%s\n今月は新店なし" % months_label
    parts = ["【新店レーダー】%s　初出 %d件" % (months_label, len(rows))]
    for r in rows:
        parts.append("\n■ %s\n%s\n許可開始 %s\n%s"
                     % (r["屋号"], r["住所"], r["許可開始日"], r["地図"]))
    return "\n".join(parts)


def send_line(text):
    load_dotenv()
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    to = os.environ.get("LINE_TO_USER_ID")
    if not token or not to:
        print("[通知スキップ] .env に LINE_CHANNEL_ACCESS_TOKEN / LINE_TO_USER_ID がありません",
              file=sys.stderr)
        return
    # LINE の1メッセージ上限は5000文字。超える場合は分割して送る。
    chunks, buf = [], ""
    for block in text.split("\n\n"):
        if len(buf) + len(block) + 2 > 4800:
            chunks.append(buf)
            buf = block
        else:
            buf = (buf + "\n\n" + block) if buf else block
    if buf:
        chunks.append(buf)

    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        json={"to": to, "messages": [{"type": "text", "text": c} for c in chunks[:5]]},
        timeout=30,
    )
    if res.status_code == 200:
        print("LINE通知 送信済み（%d通）" % len(chunks))
    else:
        print("[通知失敗] HTTP %s: %s" % (res.status_code, res.text), file=sys.stderr)


# ------------------------------------------------------------------ main

def recent_months(n):
    """直近nヶ月（当月を含まず、前月から遡る）の (令和年, 月) リスト。"""
    today = date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        out.append((y - REIWA_BASE, m))
    return list(reversed(out))


def main():
    p = argparse.ArgumentParser(description="新店レーダー（世田谷区・下北沢エリア）")
    p.add_argument("pairs", nargs="*", type=int,
                   help="令和年 月 の繰り返し。例: 8 5 8 6 8 7")
    p.add_argument("--last", type=int, help="直近Nヶ月分を自動で対象にする")
    p.add_argument("--refresh", action="store_true", help="キャッシュを無視して再取得")
    p.add_argument("--notify", action="store_true", help="初出の新店候補をLINEに送る")
    p.add_argument("--all-areas", action="store_true",
                   help="エリア絞り込みをしない（世田谷区全域）")
    args = p.parse_args()

    if args.last:
        months = recent_months(args.last)
    elif args.pairs:
        if len(args.pairs) % 2 != 0:
            p.error("引数は 令和年 月 のペアで指定してください。例: 8 5 8 6 8 7")
        months = [(args.pairs[i], args.pairs[i + 1]) for i in range(0, len(args.pairs), 2)]
    else:
        months = recent_months(1)

    label = "・".join("令和%d年%d月" % (y, m) for y, m in months)
    print("対象: %s" % label)

    chains = load_chains()
    print("チェーン除外リスト: %d 件" % len(chains))

    rows = []
    for y, m in months:
        print("[令和%d年%d月]" % (y, m))
        try:
            path = fetch_month(y, m, args.refresh)
        except FileNotFoundError as e:
            print("  %s" % e, file=sys.stderr)
            continue
        src = "令和%d年%d月" % (y, m)
        for raw in read_rows(path):
            r = normalize(raw, src, chains)
            if not r["屋号"]:
                continue  # 屋号なし＝個人名営業の可能性。個人情報配慮で落とす
            if args.all_areas or r["エリア"]:
                rows.append(r)

    # 屋号＋住所で重複排除（複数月にまたがる更新など）
    dedup = {}
    for r in rows:
        dedup.setdefault(key_of(r), r)
    rows = list(dedup.values())
    rows.sort(key=lambda r: (r["許可開始日"], r["屋号"]), reverse=True)
    print("抽出 %d 件" % len(rows))

    groups = {"個店": [], "チェーン": [], "更新": []}
    for r in rows:
        groups[r["判定"]].append(r)
    print("  個店 %d / チェーン %d / 更新とみられる %d"
          % (len(groups["個店"]), len(groups["チェーン"]), len(groups["更新"])))

    # 初出判定（更新とみられる行は通知対象外）
    state = load_state()
    new_keys = set(key_of(r) for r in rows if key_of(r) not in state)
    fresh = [r for r in groups["個店"] + groups["チェーン"] if key_of(r) in new_keys]
    print("  うち初出 %d 件（通知対象 %d 件）" % (len(new_keys), len(fresh)))

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    md_path = os.path.join(OUT_DIR, "shinten_%s.md" % stamp)
    csv_path = os.path.join(OUT_DIR, "shinten_%s.csv" % stamp)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_md(groups, label, new_keys))
    write_csv(csv_path, rows)
    all_path = merge_all_csv(rows)
    print("出力: %s" % md_path)
    print("出力: %s" % csv_path)
    print("出力: %s" % all_path)

    if args.notify:
        send_line(notify_text(fresh, label))

    # 通知まで終えてから状態を保存する（途中で落ちたら次回また初出扱いになる）
    for r in rows:
        state.setdefault(key_of(r), {"初出": stamp, "許可開始日": r["許可開始日"]})
    save_state(state)


if __name__ == "__main__":
    main()
