#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
年表 / timeline.py

これまでの出力を突き合わせて「いつ開いて、いつ閉じたか」を月ごとに並べる。

許可データから直接わかるのは許可の日付だけなので、実際の開店日・閉店日は推測になる。
根拠と確度を必ず併記し、断定しない。

  オープン … 許可開始日が下限。飲食店は許可なしに営業できないので、
              実際の開店はこの日以降。
  閉店　　 … 更新しなかった場合、許可満了日までには閉じている（上限）。
              同じ住所で別の店が先に許可を取っていれば、そちらがより厳しい上限になる。

使い方:
    python timeline.py
"""

import argparse
import csv
import glob
import os
import sys
from datetime import date

from build_map import display_name
from shinten_radar import parse_wareki, read_rows, same_unit, zen2han

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
MASTERS_DIR = os.path.join(HERE, "masters")


def read_out_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def key_of(name, addr):
    return zen2han(name) + "|" + zen2han(addr)


def first_permit_index():
    """屋号＋住所 -> 初回許可日。全許可施設一覧の最新版から引く。
    ここだけ昭和まで遡るので、その店が何年続いたかを出せる。"""
    masters = sorted(glob.glob(os.path.join(MASTERS_DIR, "zenkenr*.csv")))
    if not masters:
        return {}
    idx = {}
    for r in read_rows(masters[-1]):
        d = parse_wareki(r.get("初回許可日", ""))
        if d:
            idx[key_of(r.get("施設屋号", ""), r.get("施設所在地", ""))] = d
    return idx


def years_between(a, b):
    return (b - a).days / 365.25


def load_events():
    opens, closes = [], []

    path = os.path.join(OUT_DIR, "shinten_all.csv")
    if os.path.exists(path):
        for r in read_out_csv(path):
            if r.get("判定") == "更新" or not r.get("許可開始日"):
                continue
            opens.append({
                "屋号": display_name(r.get("屋号", "")),
                "住所": zen2han(r.get("住所", "")),
                "方書": r.get("方書", ""),
                "業種": r.get("業種", ""),
                "チェーン": r.get("判定") == "チェーン",
                "許可開始日": r["許可開始日"],
            })

    for path in sorted(glob.glob(os.path.join(OUT_DIR, "heiten_*.csv"))):
        for r in read_out_csv(path):
            if not r.get("許可満了日"):
                continue
            closes.append({
                "屋号": display_name(r.get("屋号", "")),
                "住所": zen2han(r.get("住所", "")),
                "方書": r.get("方書", ""),
                "業種": r.get("業種", ""),
                "許可満了日": r["許可満了日"],
            })
    return opens, closes


def build(opens, closes, firsts):
    by_addr = {}
    for o in opens:
        by_addr.setdefault(o["住所"], []).append(o)
    for v in by_addr.values():
        v.sort(key=lambda o: o["許可開始日"])

    events = []

    for o in opens:
        k = key_of(o["屋号"], o["住所"])
        same_addr = [c for c in closes if c["住所"] == o["住所"]]
        prev = next((c for c in same_addr if same_unit(c["方書"], o["方書"])), None)
        nearby = [c for c in same_addr if c is not prev]
        events.append({
            "kind": "open",
            "date": o["許可開始日"],
            "屋号": o["屋号"],
            "住所": o["住所"],
            "方書": o["方書"],
            "業種": o["業種"],
            "チェーン": o["チェーン"],
            "初回許可日": firsts.get(k),
            "相手": prev,
            "同番地": nearby,
        })

    for c in closes:
        k = key_of(c["屋号"], c["住所"])
        # 同じ住所で、この店の満了より前に許可を取った店があれば、そちらが厳しい上限
        same_addr = [o for o in by_addr.get(c["住所"], []) if o["屋号"] != c["屋号"]]
        succ = [o for o in same_addr if same_unit(c["方書"], o["方書"])]
        nearby = [o for o in same_addr if o not in succ]
        cap = c["許可満了日"]
        basis = "許可満了日まで更新されなかったため"
        certainty = "可能性が高い"
        s = None
        if succ:
            s = succ[0]
            certainty = "ほぼ確実"
            if s["許可開始日"] < cap:
                cap = s["許可開始日"]
                basis = "同じ区画（%s）で %s が %s に許可を取得しているため" % (
                    s["方書"], s["屋号"], s["許可開始日"])
            else:
                basis = "許可満了日まで更新されず、%s に同じ区画で %s が許可を取得" % (
                    s["許可開始日"], s["屋号"])
        events.append({
            "kind": "close",
            "date": cap,
            "屋号": c["屋号"],
            "住所": c["住所"],
            "方書": c["方書"],
            "業種": c["業種"],
            "許可満了日": c["許可満了日"],
            "初回許可日": firsts.get(k),
            "根拠": basis,
            "確度": certainty,
            "相手": s,
            "同番地": nearby,
        })

    events.sort(key=lambda e: (e["date"], e["kind"]), reverse=True)
    return events


def jp_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return "令和%d年%d月" % (y - 2018, m)


def render(events):
    L = ["# 下北沢エリア 開店・閉店 年表", "",
         "抽出日 %s" % date.today().isoformat(), "",
         "許可データからわかるのは許可の日付だけです。実際の開店日・閉店日は**推測**で、",
         "根拠と確度を各項目に書いてあります。断定する前に必ず現地で確認してください。",
         "",
         "| 種別 | 何がわかるか |",
         "|---|---|",
         "| オープン | 許可開始日が**下限**。飲食店は許可なしに営業できないので、開店はこの日以降 |",
         "| 閉店 | 更新しなかったので許可満了日までには閉じている（**上限**）。"
         "同じ**区画**で別の店が先に許可を取っていれば、そちらがより厳しい上限 |",
         "",
         "**「◯年から営業」について**：台帳の初回許可日を使っています。"
         "エリア923軒のうち132軒は1962〜2015年の実際の初回日が入っていますが、"
         "残りは現在の許可がそのまま初回として記録されています。",
         "営業者や屋号が変わると記録が新しく始まるので、**営業年数の下限**と考えてください。",
         "",
         "**「跡地」について**：番地だけでなく方書（ビル名・階）まで一致した場合にだけ跡地と書いています。"
         "同じ番地でも別のビル・別の階なら「参考」扱いです。",
         ""]

    cur = None
    for e in events:
        ym = e["date"][:7]
        if ym != cur:
            cur = ym
            L.append("")
            L.append("## %s" % jp_month(ym))
            L.append("")

        addr = ("%s　%s" % (e["住所"].replace("東京都世田谷区", ""), e["方書"])).strip()
        if e["kind"] == "open":
            tag = "🟡 オープン" + ("（チェーン）" if e["チェーン"] else "")
            L.append("**%s**　%s" % (e["屋号"], tag))
            L.append("")
            L.append("- **%s 以降にオープン**（許可開始日 %s）" % (e["date"], e["date"]))
            L.append("- %s　/　%s" % (addr, e["業種"]))
            if e["相手"]:
                L.append("- 同じ区画（%s）に **%s** があった。**跡地とみられる**"
                         % (e["方書"], e["相手"]["屋号"]))
            for n in e["同番地"]:
                L.append("- 参考：同じ番地の別区画（%s）で **%s** が閉店"
                         % (n["方書"] or "方書なし", n["屋号"]))
        else:
            L.append("**%s**　⬜ 閉店" % e["屋号"])
            L.append("")
            L.append("- **%s までに閉店**（確度：%s）" % (e["date"], e["確度"]))
            L.append("- 根拠：%s" % e["根拠"])
            if e["初回許可日"]:
                yrs = years_between(e["初回許可日"], parse_wareki(e["許可満了日"])
                                    or date.today())
                L.append("- %s から営業（**約%d年**）" % (e["初回許可日"].isoformat(), round(yrs)))
            L.append("- %s　/　%s" % (addr, e["業種"]))
            if e["相手"]:
                L.append("- 跡地に **%s**（許可開始 %s／同じ区画 %s）"
                         % (e["相手"]["屋号"], e["相手"]["許可開始日"], e["方書"]))
            for n in e["同番地"]:
                L.append("- 参考：同じ番地の別区画（%s）に %s がオープン。**跡地とは限らない**"
                         % (n["方書"] or "方書なし", n["屋号"]))
        L.append("- 確認したこと：")
        L.append("")

    L += ["---",
          "出典：「新規許可施設一覧」「全許可施設一覧」（世田谷区）",
          "※業態（ラーメン／居酒屋等）はデータに存在しません。",
          "※開店日・閉店日はいずれも許可日からの推測です。実日付ではありません。"]
    return "\n".join(L) + "\n"


def main():
    p = argparse.ArgumentParser(description="開店・閉店の年表")
    p.add_argument("--out", help="出力先（既定 out/timeline_<日付>.md）")
    args = p.parse_args()

    opens, closes = load_events()
    if not opens and not closes:
        print("材料がありません。先に shinten_radar.py と heiten_radar.py --expired を"
              "実行してください。", file=sys.stderr)
        sys.exit(1)

    firsts = first_permit_index()
    events = build(opens, closes, firsts)
    print("オープン %d 件 / 閉店 %d 件" % (len(opens), len(closes)))

    path = args.out or os.path.join(
        OUT_DIR, "timeline_%s.md" % date.today().strftime("%Y%m%d"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(events))
    print("出力: %s" % os.path.abspath(path))


if __name__ == "__main__":
    main()
