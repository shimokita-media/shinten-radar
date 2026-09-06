#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
閉店レーダー / heiten_radar.py  (世田谷区・全許可施設一覧の版間差分)

世田谷区の「全許可施設一覧」を版ごとに masters/ に保存し、
新しい版で消えている施設＝閉店の可能性がある施設を抽出する。

  https://www.city.setagaya.lg.jp/documents/3246/zenkenr{和暦YYMMDD}.csv
  例) zenkenr080331.csv  （年2回、4月20日ごろと10月15日ごろに更新される）

重要：区のサイトには最新版しか置かれていない。過去版は取得できないので、
      更新のたびに自分でアーカイブしていく必要がある。

使い方:
    python heiten_radar.py --fetch 8 3 31   # 令和8年3月31日時点の版を masters/ に保存
    python heiten_radar.py                  # masters/ の最新2版を突合して差分を出す
    python heiten_radar.py --all-areas      # エリア絞り込みなし（世田谷区全域）
"""

import argparse
import csv
import glob
import os
import sys
from datetime import date, datetime, timedelta

import requests

from shinten_radar import (REIWA_BASE, fetch_month, in_area, parse_wareki,
                           read_rows, same_unit, zen2han)

BASE_URL = "https://www.city.setagaya.lg.jp/documents/3246/zenkenr{stem}.csv"

HERE = os.path.dirname(os.path.abspath(__file__))
MASTERS_DIR = os.path.join(HERE, "masters")
OUT_DIR = os.path.join(HERE, "out")


def fetch(era_year, month, day, refresh=False):
    stem = "%02d%02d%02d" % (era_year, month, day)
    os.makedirs(MASTERS_DIR, exist_ok=True)
    path = os.path.join(MASTERS_DIR, "zenkenr%s.csv" % stem)
    if os.path.exists(path) and not refresh:
        print("既に保存済み: %s" % os.path.basename(path))
        return path
    url = BASE_URL.format(stem=stem)
    res = requests.get(url, timeout=60)
    if res.status_code != 200:
        raise FileNotFoundError("取得失敗 HTTP %s: %s" % (res.status_code, url))
    with open(path, "wb") as f:
        f.write(res.content)
    print("保存: %s (%s bytes)" % (path, format(len(res.content), ",")))
    return path


def iso(s):
    """和暦をISOに揃える。読めなければ元の文字列のまま返す。"""
    d = parse_wareki(s)
    return d.isoformat() if d else (s or "").strip()


def load_master(path):
    """屋号＋住所 -> 行 の辞書にする。"""
    out = {}
    for raw in read_rows(path):
        name = (raw.get("施設屋号", "") or "").strip()
        addr = zen2han(raw.get("施設所在地", ""))
        if not name:
            continue  # 屋号なし＝個人名営業の可能性。個人情報配慮で扱わない
        out[name + "|" + addr] = {
            "屋号": name,
            "住所": addr,
            "方書": zen2han(raw.get("施設方書", "")),
            "エリア": in_area(addr),
            "業種": (raw.get("業種", "") or "").strip(),
            "許可番号": (raw.get("許可番号", "") or "").strip(),
            "許可開始日": iso(raw.get("許可開始日", "")),
            "許可満了日": iso(raw.get("許可満了日", "")),
        }
    return out


def stem_of(path):
    return os.path.basename(path).replace("zenkenr", "").replace(".csv", "")


def label_of(path):
    s = stem_of(path)
    try:
        return "令和%d年%d月%d日版" % (int(s[0:2]), int(s[2:4]), int(s[4:6]))
    except (ValueError, IndexError):
        return s


def render_md(gone, old_path, new_path):
    L = ["# 閉店レーダー（要確認）　%s → %s" % (label_of(old_path), label_of(new_path)),
         "",
         "抽出日 %s" % datetime.now().strftime("%Y-%m-%d"),
         "",
         "旧版にあって新版で消えた施設 **%d件**。" % len(gone),
         "",
         "> **要確認**：消えた＝閉店とは限りません。",
         "> 許可満了後の更新手続き中・更新漏れ・屋号や住所の表記変更でも、突合上は「消えた」ように見えます。",
         "> 現地で確認してから扱ってください。",
         ""]
    if not gone:
        L.append("該当なし")
    for i, r in enumerate(gone, 1):
        L.append("%d. **%s**" % (i, r["屋号"]))
        L.append(("   - 住所　%s　%s" % (r["住所"], r["方書"])).rstrip())
        L.append("   - 業種　%s　/　許可 %s 〜 %s"
                 % (r["業種"], r["許可開始日"], r["許可満了日"]))
        L.append("   - 確認したこと：")
        L.append("")
    L += ["---",
          "出典：「全許可施設一覧」（世田谷区）",
          "※このリストは「閉店した店」ではなく「許可台帳から消えた施設」です。"]
    return "\n".join(L) + "\n"


# ------------------------------------------------ 満了ぶんの追跡（版が1つでも使える）

def norm_key(name, addr):
    return zen2han(name) + "|" + zen2han(addr)


def renewal_index(months, refresh=False):
    """新規許可施設一覧を月ぶん読み、屋号＋住所の集合にする。
    更新許可はこの一覧に「その月の1日開始」で載るので、更新済みかの判定に使える。
    取得できなかった月は returned の2つ目に入れて、判定保留の理由にする。"""
    seen, missing = set(), []
    for y, m in months:
        try:
            path = fetch_month(y, m, refresh)
        except FileNotFoundError:
            missing.append((y, m))
            continue
        for r in read_rows(path):
            seen.add(norm_key(r.get("施設屋号", ""), r.get("施設所在地", "")))
    return seen, missing


def next_month(y, m):
    return (y, m + 1) if m < 12 else (y + 1, 1)


def check_months(expiry):
    """満了日から、更新許可が載るはずの月（令和年, 月）を2つ返す。
    通常は満了の翌日＝翌月1日開始だが、少し遅れることもあるので次の月も見る。"""
    y, m = expiry.year, expiry.month
    if expiry.month == 12:
        first = (y + 1, 1)
    else:
        first = (y, m + 1)
    second = next_month(*first)
    return [(first[0] - REIWA_BASE, first[1]),
            (second[0] - REIWA_BASE, second[1])]


def track_expired(master_path, months_ahead, all_areas, refresh):
    """最新版の台帳から、満了日を過ぎた店・もうすぐ満了の店を洗い出す。

    この台帳には満了切れの施設が1件も残っていない（更新しなければ消える運用）。
    そこで「満了したのに新規許可一覧に再登場しない店」を閉店の可能性として拾う。"""
    today = date.today()
    rows = []
    for r in read_rows(master_path):
        name = (r.get("施設屋号", "") or "").strip()
        if not name:
            continue  # 屋号なし＝個人名営業の可能性。個人情報配慮で扱わない
        addr = zen2han(r.get("施設所在地", ""))
        if not all_areas and not in_area(addr):
            continue
        end = parse_wareki(r.get("許可満了日", ""))
        if not end:
            continue
        rows.append({
            "屋号": name,
            "住所": addr,
            "方書": zen2han(r.get("施設方書", "")),
            "エリア": in_area(addr),
            "業種": (r.get("業種", "") or "").strip(),
            "許可番号": (r.get("許可番号", "") or "").strip(),
            "許可開始日": iso(r.get("許可開始日", "")),
            "初回許可日": iso(r.get("初回許可日", "")),
            "許可満了日": end.isoformat(),
            "_end": end,
        })

    # 1軒が業種ごとに複数の許可を持つ（飲食店営業＋菓子製造業など）。
    # 判定は許可単位ではなく店単位でやる。
    shops = {}
    for r in rows:
        k = norm_key(r["屋号"], r["住所"])
        s = shops.setdefault(k, dict(r, 業種=[], _ends=[], _firsts=[]))
        s["業種"].append(r["業種"])
        s["_ends"].append(r["_end"])
        if r["初回許可日"]:
            s["_firsts"].append(r["初回許可日"])
    for s in shops.values():
        s["業種"] = "・".join(sorted(set(s["業種"])))
        s["初回許可日"] = min(s["_firsts"]) if s["_firsts"] else ""
        s["_end"] = min(s["_ends"])          # いちばん早い満了日で見る
        s["_last"] = max(s["_ends"])         # いちばん遅い満了日
        s["許可満了日"] = s["_end"].isoformat()

    # 有効な許可がまだ1つでも残っている店は営業中。閉店候補にしない
    expired = [s for s in shops.values() if s["_last"] < today]
    soon = [s for s in shops.values()
            if today <= s["_last"] <= today + timedelta(days=30 * months_ahead)]
    for s in soon:
        s["許可満了日"] = s["_last"].isoformat()
    print("下北沢エリア %d軒（許可 %d件） / すでに全許可が満了 %d軒 / %dヶ月以内に満了 %d軒"
          % (len(shops), len(rows), len(expired), months_ahead, len(soon)))

    # 満了ぶんの更新確認に必要な月をまとめて取得する
    need = sorted({mm for s in expired for mm in check_months(s["_last"])})
    seen, missing = renewal_index(need, refresh)
    missing_set = set(missing)

    gone, renewed, unknown = [], [], []
    for s in expired:
        months = check_months(s["_last"])
        if norm_key(s["屋号"], s["住所"]) in seen:
            s["判定"] = "更新を確認"
            renewed.append(s)
        elif months[0] in missing_set:
            # 更新が載るはずの月そのものが未公開なら判断できない。
            # 2つ目の月は取りこぼし用の保険なので、無くても判定はできる。
            s["判定"] = "判定保留"
            unknown.append(s)
        else:
            s["判定"] = "閉店の可能性"
            gone.append(s)

    for lst in (gone, renewed, unknown, soon):
        lst.sort(key=lambda s: s["_end"])
    return gone, renewed, unknown, soon, missing


def successors():
    """住所 -> その番地にできた新店の一覧（行データのまま返す）。
    「跡地」と言えるかは方書まで一致するかで決まるので、判定は呼び出し側でやる。
    out/shinten_all.csv（shinten_radar.py の累積出力）があるときだけ働く。"""
    path = os.path.join(OUT_DIR, "shinten_all.csv")
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("判定") == "更新":
                continue
            out.setdefault(zen2han(r.get("住所", "")), []).append(r)
    return out


def render_expired_md(gone, renewed, unknown, soon, missing, master_path, months_ahead):
    L = ["# 閉店レーダー（満了追跡）　%s" % label_of(master_path),
         "",
         "抽出日 %s" % date.today().isoformat(),
         "",
         "許可台帳には満了切れの施設が残らない（更新しないと消える）。",
         "そこで**満了日を過ぎたのに新規許可一覧に再登場しない店**を閉店の可能性として拾っている。",
         ""]
    if missing:
        L.append("> 未公開のため確認できなかった月：%s"
                 % "、".join("令和%d年%d月" % mm for mm in missing))
        L.append("")

    after = successors()

    def block(title, rows, note=""):
        L.append("## %s（%d軒）" % (title, len(rows)))
        L.append("")
        if note:
            L.append(note)
            L.append("")
        if not rows:
            L.append("該当なし")
            L.append("")
            return
        for i, r in enumerate(rows, 1):
            L.append("%d. **%s**" % (i, r["屋号"]))
            L.append(("   - 住所　%s　%s" % (r["住所"], r["方書"])).rstrip())
            L.append("   - 業種　%s　/　許可満了日　%s" % (r["業種"], r["許可満了日"]))
            L.append("   - オープン　%s"
                     % ((r["初回許可日"] + " 以降") if r.get("初回許可日") else "不明"))
            for n in after.get(r["住所"], []):
                if same_unit(r["方書"], n.get("方書", "")):
                    L.append("   - **跡地に入った新店**　%s（%s〜）／同じ区画 %s"
                             % (n.get("屋号", ""), n.get("許可開始日", ""), n.get("方書", "")))
                else:
                    L.append("   - 参考：同じ番地の新店　%s（%s〜／%s）"
                             "**別の区画なので跡地とは限らない**"
                             % (n.get("屋号", ""), n.get("許可開始日", ""),
                                n.get("方書", "") or "方書なし"))
            L.append("   - 確認したこと：")
            L.append("")

    block("閉店の可能性", gone,
          "> **要確認**：更新手続きが遅れているだけのことも、屋号や住所の表記が変わっただけのこともある。"
          "現地で確認してから扱うこと。")
    block("%dヶ月以内に満了（見張り）" % months_ahead, soon,
          "満了日が近い店。更新すれば翌月の新規許可一覧に載る。載らなければ閉店の可能性が出てくる。")
    block("判定保留（確認に必要な月がまだ未公開）", unknown)
    block("更新を確認できた", renewed,
          "満了後に新規許可一覧へ再登場した店。営業中とみてよい。")

    L += ["---",
          "出典：「全許可施設一覧」「新規許可施設一覧」（世田谷区）",
          "※このリストは「閉店した店」ではなく「許可台帳の動きから推測した候補」です。"]
    return "\n".join(L) + "\n"


def main():
    p = argparse.ArgumentParser(description="閉店レーダー（世田谷区）")
    p.add_argument("--expired", action="store_true",
                   help="最新版の台帳から、満了後に更新一覧へ戻ってこない店を洗い出す（版が1つでも使える）")
    p.add_argument("--ahead", type=int, default=3,
                   help="--expired で「もうすぐ満了」とみなす先の月数（既定3）")
    p.add_argument("--fetch", nargs=3, type=int, metavar=("令和年", "月", "日"),
                   help="指定日付版を masters/ に保存する。例: --fetch 8 3 31")
    p.add_argument("--old", help="旧版のパス（省略時は masters/ の古い方から自動選択）")
    p.add_argument("--new", help="新版のパス（省略時は masters/ の最新）")
    p.add_argument("--all-areas", action="store_true",
                   help="エリア絞り込みをしない（世田谷区全域）")
    p.add_argument("--refresh", action="store_true", help="保存済みでも再取得する")
    args = p.parse_args()

    if args.fetch:
        fetch(args.fetch[0], args.fetch[1], args.fetch[2], args.refresh)

    masters = sorted(glob.glob(os.path.join(MASTERS_DIR, "zenkenr*.csv")),
                     key=stem_of)

    if args.expired:
        if not masters:
            print("masters/ に台帳がありません。先に --fetch 8 3 31 のように取得してください。",
                  file=sys.stderr)
            sys.exit(1)
        master = masters[-1]
        print("台帳: %s (%s)" % (os.path.basename(master), label_of(master)))
        gone, renewed, unknown, soon, missing = track_expired(
            master, args.ahead, args.all_areas, args.refresh)
        print("閉店の可能性 %d / 更新を確認 %d / 判定保留 %d / まもなく満了 %d"
              % (len(gone), len(renewed), len(unknown), len(soon)))

        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = date.today().strftime("%Y%m%d")
        md_path = os.path.join(OUT_DIR, "heiten_expired_%s.md" % stamp)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_expired_md(gone, renewed, unknown, soon, missing,
                                      master, args.ahead))
        # 地図に載せるのは閉店の可能性だけ。build_map.py が heiten_*.csv を拾う
        csv_path = os.path.join(OUT_DIR, "heiten_expired_%s.csv" % stamp)
        cols = ["屋号", "住所", "方書", "エリア", "業種", "許可番号",
                "初回許可日", "許可開始日", "許可満了日", "判定"]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(gone)
        print("出力: %s" % md_path)
        print("出力: %s" % csv_path)
        return

    if args.old and args.new:
        old_path, new_path = args.old, args.new
    elif len(masters) >= 2:
        old_path, new_path = masters[-2], masters[-1]
    else:
        print("masters/ に版が %d 個しかありません。突合には2版必要です。" % len(masters))
        print("区のサイトには最新版しか置かれていないため、次回の更新（4月20日ごろ／10月15日ごろ）に")
        print("  python heiten_radar.py --fetch <令和年> <月> <日>")
        print("を実行して版を貯めてください。今ある版:")
        for m in masters:
            print("  - %s  (%s)" % (os.path.basename(m), label_of(m)))
        sys.exit(0 if masters else 1)

    print("旧版: %s" % os.path.basename(old_path))
    old = load_master(old_path)
    print("新版: %s" % os.path.basename(new_path))
    new = load_master(new_path)

    gone = [v for k, v in old.items() if k not in new]
    if not args.all_areas:
        gone = [r for r in gone if r["エリア"]]
    gone.sort(key=lambda r: (r["エリア"], r["屋号"]))
    print("消えた施設 %d 件（旧版 %d / 新版 %d）" % (len(gone), len(old), len(new)))

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "heiten_%s_%s.md" % (stem_of(old_path), stem_of(new_path)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_md(gone, old_path, new_path))

    csv_path = path.replace(".md", ".csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        cols = ["屋号", "住所", "方書", "エリア", "業種", "許可番号", "許可開始日", "許可満了日"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(gone)

    print("出力: %s" % path)
    print("出力: %s" % csv_path)


if __name__ == "__main__":
    main()
