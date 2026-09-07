#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公開前の検査 / verify_public.py

自動更新で「おかしな地図」が公開されるのを止めるための門番。
1つでも引っかかったら異常終了し、GitHub Actions がそこで止まる（公開されない）。

区がCSVの形式を変えた、取得に失敗した、といったときに黙って壊れた地図が
出ていくのを防ぐのが目的。

使い方:
    python verify_public.py
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "docs", "index.html")
BASE_URL = "https://shimokita-media.github.io/shinten-radar"

errors = []
notes = []


def check(cond, msg):
    if not cond:
        errors.append(msg)
    return cond


def data_of(html):
    m = re.search(r"const DATA = (\[.*?\]);\s*\r?\n", html, re.S)
    return json.loads(m.group(1)) if m else None


def previous_count():
    """1つ前のコミットに入っている地図の件数。無ければ None。"""
    try:
        out = subprocess.run(["git", "show", "HEAD:docs/index.html"],
                             cwd=HERE, capture_output=True, timeout=30)
        if out.returncode != 0:
            return None
        d = data_of(out.stdout.decode("utf-8"))
        return len(d) if d is not None else None
    except Exception:  # noqa: BLE001
        return None


def main():
    if not check(os.path.exists(TARGET), "docs/index.html が無い"):
        report()

    html = open(TARGET, encoding="utf-8").read()
    size = len(html.encode())

    d = data_of(html)
    if not check(d is not None, "地図データ(DATA)を読み取れない。HTMLの構造が壊れている"):
        report()

    check(len(d) > 0, "地図に1件も載っていない。取得か抽出に失敗している")
    check(size > 5000, "HTMLが小さすぎる（%d bytes）。生成に失敗している" % size)

    # 公開してはいけないものが混ざっていないか
    gone = [x for x in d if x.get("kind") == "閉店の可能性"]
    check(not gone,
          "「閉店の可能性」が %d 件混ざっている。推測を実名公開してはいけない" % len(gone))
    check("営業者" not in html and "電話" not in html,
          "営業者名か電話番号が混ざっている疑い")
    check(all(x.get("name") for x in d), "屋号が空の行が混ざっている")

    # ライセンス条件（CC BY）を満たしているか
    check("世田谷区" in html, "出典表示（世田谷区）が消えている。CC BY 違反になる")
    check("OpenStreetMap" in html, "地図の著作権表示（OpenStreetMap）が消えている")

    # 共有時のリンク先
    m = re.search(r'og:url" content="([^"]+)', html)
    check(m and m.group(1).startswith(BASE_URL),
          "og:url が %s になっていない（--base-url の指定漏れ）" % BASE_URL)

    # 件数が減っていないか。新店は積み上げなので減ることはないはず
    prev = previous_count()
    if prev is None:
        notes.append("前回の件数を取得できなかったので、増減の比較は省略")
    else:
        check(len(d) >= prev,
              "件数が %d 件から %d 件に減っている。データの取りこぼしが疑われる"
              % (prev, len(d)))
        notes.append("前回 %d 件 → 今回 %d 件" % (prev, len(d)))

    notes.append("%d 件 / %.1f KB" % (len(d), size / 1024))
    report()


def report():
    for n in notes:
        print("  " + n)
    if errors:
        print("\n公開を中止します。以下が引っかかりました:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
    print("検査を通過しました。公開して問題ありません。")
    sys.exit(0)


if __name__ == "__main__":
    main()
