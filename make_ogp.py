#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OGP画像ビルダー / make_ogp.py

SNSにURLを貼ったときに出るサムネイル（1200x630）を作る。
地図と同じ黄と黒。文字だけなので、地図の中身が変わっても作り直さなくてよい。

使い方:
    python make_ogp.py --count 21 --period 令和8年5月〜令和8年7月
"""

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

INK = (20, 19, 17)
HI = (255, 212, 0)
DIM = (228, 224, 210)

# Windows標準の日本語フォント。上から順に見つかったものを使う
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\YuGothB.ttc", 0),
    (r"C:\Windows\Fonts\meiryob.ttc", 0),
    (r"C:\Windows\Fonts\msgothic.ttc", 0),
]


def load_font(size):
    for path, index in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return None


def build(count, period, out_path):
    big = load_font(76)
    mid = load_font(34)
    small = load_font(26)
    if not big:
        print("日本語フォントが見つかりませんでした。OGP画像は作りません。",
              file=sys.stderr)
        return None

    img = Image.new("RGB", (1200, 630), INK)
    d = ImageDraw.Draw(img)

    # 上端に黄色の帯（地図のマストヘッドと同じ見え方にする）
    d.rectangle([0, 0, 1200, 14], fill=HI)

    d.text((80, 150), "下北沢", font=big, fill=HI)
    d.text((80, 250), "新店レーダー", font=big, fill=HI)
    d.text((80, 380), period, font=mid, fill=DIM)
    d.text((80, 432), "%d軒" % count, font=mid, fill=HI)
    d.text((80, 520), "世田谷区の営業許可オープンデータから起こした新店の候補",
           font=small, fill=DIM)
    d.text((80, 558), "出典：「新規許可施設一覧」（世田谷区）",
           font=small, fill=(150, 146, 136))

    # 右下に凡例と同じ丸を置く
    for i, (fill, outline) in enumerate([(HI, INK), (INK, HI)]):
        x = 1000 + i * 70
        d.ellipse([x, 250, x + 46, 296], fill=fill, outline=outline, width=5)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def main():
    p = argparse.ArgumentParser(description="OGP画像を作る")
    p.add_argument("--count", type=int, default=0)
    p.add_argument("--period", default="")
    p.add_argument("--out", default=os.path.join(HERE, "docs", "ogp.png"))
    args = p.parse_args()
    path = build(args.count, args.period, args.out)
    if path:
        print("出力: %s (%s bytes)"
              % (path, format(os.path.getsize(path), ",")))


if __name__ == "__main__":
    main()
