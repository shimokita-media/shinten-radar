#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地図ビルダー / build_map.py

out/shinten_all.csv（＋あれば out/heiten_*.csv）を読み、住所から緯度経度を引いて
Leaflet の単一HTMLファイルを書き出す。CDN以外の外部依存なし、1ファイルで完結。

ジオコーディング:
  1) 国土地理院 住所検索API（https://msearch.gsi.go.jp/address-search/AddressSearch）
  2) 失敗したら OpenStreetMap Nominatim
  いずれも無料。結果は data/geocache.json にキャッシュするので2回目以降は叩かない。
  Nominatim の利用規約に従い 1秒1リクエストに制限している。

使い方:
    python build_map.py
    python build_map.py --out out/map.html
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
import unicodedata

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
CACHE_PATH = os.path.join(HERE, "data", "geocache.json")

GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
UA = "shinten-radar/1.0 (local tool; contact: shimokita media)"

# 下北沢駅あたり。ジオコーディングが全滅したときの初期表示位置。
DEFAULT_CENTER = [35.6614, 139.6680]

# 世田谷区からあまりに離れた座標は誤ヒットとみなして捨てる
BBOX = (35.60, 35.70, 139.58, 139.70)  # lat_min, lat_max, lon_min, lon_max


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)


KANSUJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def normalize_address(addr):
    """'…北沢二丁目14番8号' -> '…北沢2-14-8'。ジオコーダが解釈しやすい形にする。"""
    s = addr.strip()
    m = re.search(r"([一二三四五六七八九十]+)丁目", s)
    if m:
        word = m.group(1)
        if word == "十":
            num = 10
        elif word.startswith("十"):
            num = 10 + KANSUJI.get(word[1:], 0)
        elif word.endswith("十"):
            num = KANSUJI.get(word[0], 0) * 10
        else:
            num = KANSUJI.get(word, 0)
        if num:
            s = s.replace(m.group(0), "%d-" % num)
    s = re.sub(r"(\d+)番地?", r"\1-", s)
    s = re.sub(r"(\d+)号.*$", r"\1", s)
    s = re.sub(r"-+", "-", s).rstrip("-")
    return s


def in_bbox(lat, lon):
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def geocode_gsi(q):
    res = requests.get(GSI_URL, params={"q": q}, timeout=20,
                       headers={"User-Agent": UA})
    if res.status_code != 200:
        return None
    data = res.json()
    if not data:
        return None
    lon, lat = data[0]["geometry"]["coordinates"][:2]
    return (float(lat), float(lon))


def geocode_nominatim(q):
    res = requests.get(NOMINATIM_URL,
                       params={"q": q, "format": "json", "limit": 1,
                               "countrycodes": "jp"},
                       timeout=20, headers={"User-Agent": UA})
    if res.status_code != 200:
        return None
    data = res.json()
    if not data:
        return None
    return (float(data[0]["lat"]), float(data[0]["lon"]))


def geocode(addr, cache):
    if addr in cache:
        c = cache[addr]
        return tuple(c) if c else None

    q = normalize_address(addr)
    got = None
    for fn, name, wait in ((geocode_gsi, "GSI", 0.2),
                           (geocode_nominatim, "Nominatim", 1.1)):
        try:
            got = fn(q)
        except Exception as e:  # noqa: BLE001
            print("  [%s 失敗] %s: %s" % (name, q, e), file=sys.stderr)
            got = None
        time.sleep(wait)
        if got and in_bbox(*got):
            break
        if got:
            print("  [範囲外につき破棄] %s -> %s" % (q, got), file=sys.stderr)
            got = None
    cache[addr] = list(got) if got else None
    return got


# ------------------------------------------------------------------ 読み込み

def read_csv_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def collect():
    """(種別, 行) のリストを作る。種別は 新店 / チェーン / 閉店の可能性。"""
    items = []
    shinten = os.path.join(OUT_DIR, "shinten_all.csv")
    if os.path.exists(shinten):
        for r in read_csv_rows(shinten):
            if r.get("判定") == "更新":
                continue  # 更新とみられる行は地図に出さない
            kind = "チェーン" if r.get("判定") == "チェーン" else "新店"
            items.append((kind, r))
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "heiten_*.csv"))):
        for r in read_csv_rows(path):
            items.append(("閉店の可能性", r))
    return items


def display_name(name):
    """表示用に屋号を整える。'ＯＣＨＥＲ' -> 'OCHER'。
    一覧で目が滑らないようにするためで、CSV側の元表記はいじらない。"""
    return unicodedata.normalize("NFKC", name or "").strip()


ISO_MONTH = re.compile(r"^\d{4}-\d{2}$")


def iso_months(items):
    """新店側のISO年月だけを返す。閉店行の許可開始日は何年も前なので、
    見出しの期間や「最新月」の判定には混ぜない。"""
    return sorted(set(m for m in (r.get("許可開始日", "")[:7]
                                  for k, r in items if k != "閉店の可能性")
                      if ISO_MONTH.match(m)))


def period_label(items):
    months = iso_months(items)
    if not months:
        return ""

    def jp(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        return "令和%d年%d月" % (y - 2018, m)

    if len(months) == 1:
        return jp(months[0])
    return "%s〜%s" % (jp(months[0]), jp(months[-1]))


# ------------------------------------------------------------------ HTML

HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>下北沢 新店レーダー</title>
__META__
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=DotGothic16&family=Zen+Kaku+Gothic+New:wght@400;500;700;900&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
/* ---- 黄と黒 ---------------------------------------------------------
   下北沢の小劇場の立て看板やライブハウスのフライヤーの配色。
   黒地に黄はコントラスト比13:1で、屋外の明るい場所でも文字が飛ばない。
   地図の下地だけは紙色のまま残す。街路名が読めなくなると用をなさないため。 */
:root{
  color-scheme: light;
  --ink:      #141311;
  --hi:       #FFD400;
  --paper:    #F4F1E6;
  --paper-hi: #FBF9F2;
  --dim:      #E4E0D2;
  --sub:      #4A463E;
  --sans: "Zen Kaku Gothic New", -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
  /* 見出しは本文と同じ family の最重量。字の中の空きが広く、小さい画面でも潰れない。
     もっと癖を出したいときは、上の <link> に family を足したうえで
     "Zen Maru Gothic"（丸ゴシック）や "RocknRoll One" に差し替える。 */
  --disp: var(--sans);
  --dot:  "DotGothic16", ui-monospace, monospace;
  --head-h: 150px;   /* 読み込み後にJSで実測値へ差し替える */
  --bar-h: 58px;
}
*{ box-sizing:border-box; }
html,body{ margin:0; padding:0; height:100%; overflow:hidden;
  background:var(--paper); color:var(--ink); font-family:var(--sans);
  -webkit-text-size-adjust:100%; }
button{ font:inherit; color:inherit; }
:focus-visible{ outline:3px solid var(--ink); outline-offset:2px; }
.head :focus-visible{ outline-color:var(--hi); }

/* ---- 地図 ---- */
#map{ position:absolute; left:0; right:0; top:var(--head-h); bottom:var(--bar-h);
  background:var(--paper); }
/* 下地を脱色して紙色に寄せる。黄と黒のピンだけが立つようにするため */
.leaflet-tile{ filter:grayscale(1) contrast(.72) brightness(1.14) sepia(.3); }
.leaflet-container{ background:var(--paper); font-family:var(--sans); }

/* ---- マストヘッド：黒の帯 ---- */
.head{ position:absolute; z-index:1000; top:0; left:0; right:0;
  background:var(--ink); border-bottom:3px solid var(--hi);
  padding:11px 14px 10px; }
.title{ font-family:var(--disp); font-weight:900; font-size:28px; line-height:1.15;
  margin:0; color:var(--hi); letter-spacing:.03em; }
.period{ font-size:13px; font-weight:500; margin:8px 0 0;
  color:var(--hi); letter-spacing:.02em; }
.caveat{ font-size:12px; font-weight:500; line-height:1.6; margin:6px 0 10px;
  color:var(--dim); }
.filters{ display:flex; gap:7px; flex-wrap:wrap; }
.chip{ display:inline-flex; align-items:center; gap:7px; cursor:pointer;
  background:transparent; color:var(--hi); border:2px solid var(--hi);
  border-radius:999px; padding:7px 13px; font-size:13.5px; font-weight:700;
  line-height:1; }
.chip[aria-pressed="true"]{ background:var(--hi); color:var(--ink); }
.chip[aria-pressed="false"]{ opacity:.55; }
.chip .sw{ width:12px; height:12px; border-radius:50%; flex:none;
  border:2px solid; box-shadow:0 0 0 2px var(--paper-hi); }
.chip .n{ font-size:13.5px; font-weight:900; }

/* ---- ピン：塗り＝営業中、中空＝台帳から消えた店 ---- */
.pin{ width:40px; height:40px; }
.pin i{ position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
  width:19px; height:19px; border-radius:50%; display:block; border:3px solid; }
.pin i.gone, .row-sw.gone, .chip .sw.gone{ border-style:dashed; }

/* ---- ふきだし ---- */
.leaflet-popup-content-wrapper{ background:var(--ink); color:var(--paper-hi);
  border:3px solid var(--hi); border-radius:3px;
  box-shadow:5px 5px 0 rgba(20,19,17,.3); }
.leaflet-popup-content{ margin:13px 15px; font-size:13px; line-height:1.6; }
.leaflet-popup-tip{ background:var(--hi); border:0; box-shadow:none; }
.leaflet-popup-close-button{ color:var(--hi) !important; font-size:21px !important;
  padding:7px 9px 0 0 !important; }
.pop-kind{ display:inline-block; font-size:11px; font-weight:700; line-height:1;
  padding:5px 8px; border:2px solid; border-radius:999px; margin-bottom:8px; }
.pop-name{ font-family:var(--disp); font-weight:900; font-size:18.5px;
  line-height:1.35; margin:0 0 7px; color:var(--hi); }
.pop-dl{ margin:0 0 11px; font-size:13px; line-height:1.5;
  display:grid; grid-template-columns:auto 1fr; gap:3px 10px; }
.pop-dl dt{ color:var(--dim); font-size:11.5px; font-weight:700; padding-top:1px; }
.pop-dl dd{ margin:0; color:var(--paper-hi); font-weight:500; }
.pop-dl b{ font-family:var(--dot); font-weight:400; letter-spacing:.05em; }
.pop-addr{ font-size:12.5px; color:var(--dim); margin:0 0 11px; line-height:1.55; }
.leaflet-popup-content a.pop-go{ display:inline-block; background:var(--hi);
  color:var(--ink); text-decoration:none; font-size:13px; font-weight:900;
  padding:10px 15px; border-radius:2px; }
.leaflet-popup-content a.pop-go:hover{ background:var(--paper-hi); }

/* ---- 下からせり上がるリスト ---- */
.drawer{ position:absolute; z-index:1001; left:0; right:0; bottom:0;
  background:var(--paper-hi); border-top:3px solid var(--ink);
  display:flex; flex-direction:column;
  height:var(--bar-h); max-height:78%;
  transition:height .28s cubic-bezier(.22,.61,.36,1);
  padding-bottom:env(safe-area-inset-bottom); }
.drawer[data-open="true"]{ height:min(64%, 470px); }
.handle{ flex:none; height:var(--bar-h); width:100%; cursor:pointer;
  background:var(--hi); color:var(--ink); border:0;
  border-bottom:3px solid var(--ink);
  display:flex; align-items:center; gap:10px; padding:0 14px; text-align:left; }
.handle-arrow{ font-size:11px; line-height:1; transition:transform .28s; }
.drawer[data-open="true"] .handle-arrow{ transform:rotate(180deg); }
.handle-l{ font-weight:900; font-size:15px; }
.handle-n{ font-size:14px; font-weight:700; margin-left:auto; }
.list{ overflow-y:auto; -webkit-overflow-scrolling:touch; flex:1; }
.row{ display:flex; align-items:flex-start; gap:11px; width:100%;
  padding:12px 14px; background:none; border:0; cursor:pointer; text-align:left;
  border-bottom:1px solid rgba(20,19,17,.15); }
.row:hover{ background:rgba(20,19,17,.05); }
.row-sw{ width:14px; height:14px; border-radius:50%; flex:none; margin-top:4px;
  border:2.5px solid; }
.row-main{ flex:1; min-width:0; }
.row-name{ display:block; font-weight:700; font-size:15.5px; line-height:1.4;
  overflow-wrap:anywhere; }
.row-addr{ display:block; font-size:12.5px; color:var(--sub); margin-top:4px;
  line-height:1.5; overflow-wrap:anywhere; }
.row-dates{ display:block; font-size:12px; color:var(--sub); margin-top:4px;
  line-height:1.6; }
.row-dates b{ font-family:var(--dot); font-weight:400; letter-spacing:.04em;
  color:var(--ink); }
.empty{ padding:26px 16px; font-size:13px; color:var(--sub); }
.src{ flex:none; margin:0; padding:11px 14px 13px; font-size:11px;
  line-height:1.6; color:var(--sub); border-top:1px solid rgba(20,19,17,.15); }

.leaflet-control-attribution{ font-size:9.5px !important;
  background:rgba(251,249,242,.9) !important; }

@media (min-width:760px){
  .title{ font-size:32px; }
  .drawer{ left:auto; right:0; top:0; bottom:0; width:352px;
    height:auto !important; max-height:none;
    border-top:0; border-left:3px solid var(--ink); }
  .handle{ display:none; }
  .head{ right:352px; }
  #map{ right:352px; bottom:0; }
}
@media (max-height:560px){
  .leaflet-control-zoom{ display:none; }
}
@media (prefers-reduced-motion:reduce){
  .drawer,.handle-arrow{ transition:none; }
}
</style>
</head>
<body>

<header class="head">
  <h1 class="title">下北沢 新店レーダー</h1>
  <p class="period">__PERIOD__</p>
  <p class="caveat">許可データから起こした候補リストです。業態は含まれません。廃業は反映されません。</p>
  <div class="filters" id="filters"></div>
</header>

<div id="map"></div>

<div class="drawer" id="drawer" data-open="false">
  <button class="handle" id="handle" aria-expanded="false" aria-controls="list">
    <span class="handle-arrow" aria-hidden="true">▲</span>
    <span class="handle-l" id="handleL">リストで見る</span>
    <span class="handle-n" id="handleN"></span>
  </button>
  <div class="list" id="list"></div>
  <p class="src">
    出典：「新規許可施設一覧」「全許可施設一覧」（世田谷区）／地図 &copy; OpenStreetMap contributors<br>
    「閉店の可能性」は許可台帳から消えた施設です。更新手続き中のこともあるので現地で確認してください。
  </p>
</div>

<script>
const KINDS = __KINDS__;
const DATA = __DATA__;
const CENTER = __CENTER__;

const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const esc = s => (s || '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const shortAddr = s => (s || '').replace(/^東京都世田谷区/, '');
const swatch = k => 'background:' + KINDS[k].fill + ';border-color:' + KINDS[k].line;
const goneCls = k => KINDS[k].gone ? ' gone' : '';

const map = L.map('map', { zoomControl: false, attributionControl: true,
  fadeAnimation: false })
  .setView(CENTER, 16);
L.control.zoom({ position: 'bottomleft' }).addTo(map);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}).addTo(map);

const shown = new Set(Object.keys(KINDS));
const markers = DATA.map((d, i) => {
  const icon = L.divIcon({
    className: '',
    html: '<div class="pin"><i class="' + (KINDS[d.kind].gone ? 'gone' : '') +
          '" style="' + swatch(d.kind) + '"></i></div>',
    iconSize: [40, 40], iconAnchor: [20, 20], popupAnchor: [0, -16]
  });
  const m = L.marker([d.lat, d.lon], { icon, title: d.name, riseOnHover: true });
  m.bindPopup(
    '<span class="pop-kind" style="' + swatch(d.kind) + ';color:' +
      KINDS[d.kind].text + '">' + esc(d.kind) + '</span>' +
    '<h2 class="pop-name">' + esc(d.name) + '</h2>' +
    '<dl class="pop-dl">' +
      '<dt>オープン</dt><dd>' + (d.since
        ? '<b>' + esc(d.since) + '</b> 以降' : '不明') + '</dd>' +
      (KINDS[d.kind].gone
        ? '<dt>閉店</dt><dd><b>' + esc(d.date || '----') + '</b> までに</dd>' : '') +
      '<dt>業種</dt><dd>' + esc(d.category || '不明') + '</dd>' +
    '</dl>' +
    '<p class="pop-addr">' + esc(shortAddr(d.address)) +
      (d.sub ? '<br>' + esc(d.sub) : '') + '</p>' +
    '<a class="pop-go" target="_blank" rel="noopener" href="' +
      'https://www.google.com/maps/search/?api=1&query=' +
      encodeURIComponent(d.name + ' ' + d.address) + '">Googleマップで開く</a>',
    { maxWidth: 262 }
  );
  m._i = i;
  return m;
});

const layer = L.layerGroup(markers).addTo(map);

/* ---- 絞り込み ---- */
const filters = document.getElementById('filters');
for (const kind of Object.keys(KINDS)) {
  const n = DATA.filter(d => d.kind === kind).length;
  if (!n) continue;
  const b = document.createElement('button');
  b.className = 'chip';
  b.type = 'button';
  b.setAttribute('aria-pressed', 'true');
  b.innerHTML = '<span class="sw' + goneCls(kind) + '" style="' + swatch(kind) +
                '"></span>' +
                esc(kind) + ' <span class="n">' + n + '</span>';
  b.onclick = () => {
    const on = b.getAttribute('aria-pressed') === 'true';
    b.setAttribute('aria-pressed', on ? 'false' : 'true');
    if (on) shown.delete(kind); else shown.add(kind);
    render();
  };
  filters.appendChild(b);
}

/* ---- リスト ---- */
const list = document.getElementById('list');
const handleN = document.getElementById('handleN');
const drawer = document.getElementById('drawer');
const handle = document.getElementById('handle');
const handleL = document.getElementById('handleL');

handle.onclick = () => {
  const open = drawer.dataset.open === 'true';
  drawer.dataset.open = open ? 'false' : 'true';
  handle.setAttribute('aria-expanded', open ? 'false' : 'true');
  handleL.textContent = open ? 'リストで見る' : '地図にもどる';
};

function focusOn(i) {
  const to = [DATA[i].lat, DATA[i].lon];
  if (REDUCED) map.setView(to, 18); else map.flyTo(to, 18, { duration: .6 });
  markers[i].openPopup();
  if (window.innerWidth < 760) {
    drawer.dataset.open = 'false';
    handle.setAttribute('aria-expanded', 'false');
    handleL.textContent = 'リストで見る';
  }
}

function render() {
  const visible = DATA.map((d, i) => [d, i]).filter(([d]) => shown.has(d.kind));
  layer.clearLayers();
  visible.forEach(([, i]) => layer.addLayer(markers[i]));

  handleN.textContent = visible.length + '軒';
  list.innerHTML = '';
  if (!visible.length) {
    list.innerHTML = '<p class="empty">表示する店がありません。上のボタンで種類を選び直してください。</p>';
    return;
  }
  for (const [d, i] of visible) {
    const b = document.createElement('button');
    b.className = 'row';
    b.type = 'button';
    b.innerHTML =
      '<span class="row-sw' + goneCls(d.kind) + '" style="' + swatch(d.kind) + '"></span>' +
      '<span class="row-main">' +
        '<span class="row-name">' + esc(d.name) + '</span>' +
        '<span class="row-addr">' + esc(shortAddr(d.address)) +
          (d.sub ? ' ' + esc(d.sub) : '') + '</span>' +
        '<span class="row-dates">オープン ' +
          (d.since ? '<b>' + esc(d.since) + '</b> 以降' : '不明') +
          (KINDS[d.kind].gone
            ? '<br>閉店　　<b>' + esc(d.date || '----') + '</b> までに' : '') +
        '</span>' +
      '</span>';
    b.onclick = () => focusOn(i);
    list.appendChild(b);
  }
}

render();

// 見出しは文字の折り返しで高さが変わる。実測して地図の上端に反映する
function layout() {
  const h = document.querySelector('.head').offsetHeight;
  document.documentElement.style.setProperty('--head-h', h + 'px');
  map.invalidateSize({ animate: false });
}
layout();
window.addEventListener('resize', layout);

if (DATA.length) {
  map.fitBounds(DATA.map(d => [d.lat, d.lon]), { padding: [28, 28], maxZoom: 17 });
}
</script>
</body>
</html>
"""

INK = "#141311"
HI = "#FFD400"
PAPER = "#F4F1E6"

# 色だけでなく形でも見分けられるようにする。
# 塗り＝いま営業しているはずの店 / 中空＝台帳から消えた店。
KINDS = {
    "新店":        {"fill": HI,    "line": INK, "text": INK, "gone": False},
    "チェーン":     {"fill": INK,   "line": HI,  "text": HI,  "gone": False},
    "閉店の可能性": {"fill": PAPER, "line": INK, "text": INK, "gone": True},
}


DESCRIPTION = ("世田谷区の営業許可オープンデータから起こした、"
               "下北沢エリア（北沢・代沢・代田・大原・羽根木）の新店の候補地図。")


def meta_tags(args, period, count):
    """SNSにURLを貼ったときのカード。公開版のときだけ入れる。"""
    if not args.public:
        return ""
    base = (args.base_url or "").rstrip("/")
    title = "下北沢 新店レーダー"
    desc = "%s　%s／%d軒" % (DESCRIPTION, period, count)
    tags = [
        '<meta name="description" content="%s">' % desc,
        '<meta property="og:type" content="website">',
        '<meta property="og:title" content="%s">' % title,
        '<meta property="og:description" content="%s">' % desc,
        '<meta property="og:site_name" content="%s">' % title,
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if base:
        tags.append('<meta property="og:url" content="%s/">' % base)
        tags.append('<meta property="og:image" content="%s/ogp.png">' % base)
    else:
        print("[注意] --base-url が無いので og:image を絶対URLにできません。"
              "SNSでサムネイルが出ません。", file=sys.stderr)
    return "\n".join(tags)


def make_ogp(period, count):
    """OGP画像を作る。Pillow が無ければ黙って飛ばす（地図自体は成立するため）。"""
    try:
        from make_ogp import build
    except ImportError:
        print("[注意] Pillow が無いのでOGP画像は作りません（pip install pillow）",
              file=sys.stderr)
        return
    path = build(count, period, os.path.join(HERE, "docs", "ogp.png"))
    if path:
        print("出力: %s" % path)


def main():
    p = argparse.ArgumentParser(description="新店レーダー 地図ビルダー")
    p.add_argument("--out", default=os.path.join(OUT_DIR, "map.html"))
    p.add_argument("--limit", type=int, help="先頭N件だけ処理する（動作確認用）")
    p.add_argument("--public", action="store_true",
                   help="公開版を docs/index.html に書き出す。"
                        "「閉店の可能性」は推測なので実名公開しない（除外する）")
    p.add_argument("--base-url",
                   help="公開版のURL。OGP画像の絶対URLに使う。"
                        "例: https://USER.github.io/shinten-radar/")
    args = p.parse_args()

    items = collect()
    if args.public:
        # 「閉店の可能性」は台帳の動きからの推測にすぎない。
        # 営業中の店を閉店扱いすると実害が出るので、公開版には載せない。
        before = len(items)
        items = [(k, r) for k, r in items if k != "閉店の可能性"]
        print("公開版：閉店の可能性 %d 件を除外" % (before - len(items)))
    if args.limit:
        items = items[:args.limit]
    if not items:
        print("対象がありません。先に shinten_radar.py を実行してください。", file=sys.stderr)
        sys.exit(1)
    print("対象 %d 件" % len(items))

    period = period_label(items)

    cache = load_cache()
    points, failed = [], []
    try:
        for i, (kind, r) in enumerate(items, 1):
            addr = r.get("住所", "")
            if not addr:
                continue
            cached = addr in cache
            ll = geocode(addr, cache)
            if not ll:
                failed.append(r.get("屋号", "") + " / " + addr)
                continue
            gone = kind == "閉店の可能性"
            date = r.get("許可満了日", "") if gone else r.get("許可開始日", "")
            # 初回許可日＝台帳に記録された最初の許可。いつからやっている店かの下限。
            # 無い行もあるので、その場合は空にして地図側で「不明」と出す
            since = r.get("初回許可日", "") or r.get("許可開始日", "")
            sub = r.get("方書", "")
            points.append({
                "kind": kind,
                "name": display_name(r.get("屋号", "")),
                "address": addr,
                "sub": "" if sub == "-" else sub,
                "category": r.get("業種", ""),
                "date": date,
                "since": since,
                "lat": ll[0], "lon": ll[1],
            })
            if not cached:
                print("  %d/%d %s -> %.5f,%.5f" % (i, len(items), addr, ll[0], ll[1]))
    finally:
        save_cache(cache)

    # 新しい順。リストは上から見るので、鮮度の高いものが先に来るようにする
    points.sort(key=lambda p: p["date"], reverse=True)

    print("測位できた %d 件 / できなかった %d 件" % (len(points), len(failed)))
    for f in failed:
        print("  [未測位] %s" % f)

    center = DEFAULT_CENTER
    if points:
        center = [sum(p["lat"] for p in points) / len(points),
                  sum(p["lon"] for p in points) / len(points)]

    label = period
    if points:
        label += "\u3000/\u3000%d軒" % len(points)

    html = (HTML
            .replace("__META__", meta_tags(args, period, len(points)))
            .replace("__KINDS__", json.dumps(KINDS, ensure_ascii=False))
            .replace("__DATA__", json.dumps(points, ensure_ascii=False))
            .replace("__CENTER__", json.dumps(center))
            .replace("__PERIOD__", label))
    out_path = args.out
    if args.public and out_path == os.path.join(OUT_DIR, "map.html"):
        out_path = os.path.join(HERE, "docs", "index.html")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("出力: %s" % os.path.abspath(out_path))

    if args.public:
        make_ogp(period, len(points))


if __name__ == "__main__":
    main()
