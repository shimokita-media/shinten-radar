# 公開手順（GitHub Pages）

公開するのは `docs/index.html` の1ファイルだけ。地図はこれ単体で動く。

## 公開に関する前提

**ライセンス** — 世田谷区のCSVは **CC BY 4.0**。区のページに「二次利用可能です」と明記されている。
出典表示が条件なので、地図フッターの

> 出典：「新規許可施設一覧」「全許可施設一覧」（世田谷区）／地図 © OpenStreetMap contributors

を**絶対に消さないこと**。消すとライセンス違反になる。

**「閉店の可能性」は公開しない** — `--public` で自動的に除外される。
あれは許可台帳の動きからの推測であって事実ではない。更新手続きが遅れているだけの店を
「閉店の可能性」として実名で公開すると、そのお店の営業に実害が出る。
裏が取れた店だけ、記事として個別に出すこと。

**公開HTMLに個人情報は入っていない** — 屋号・住所・業種・許可日・座標のみ。
営業者名と電話番号は含まない。屋号が空の行（自宅営業の可能性）は抽出時に落としている。

**地図タイル** — OpenStreetMapの公式タイルサーバーを使っている。
個人サイト規模なら問題ないが、アクセスが増えたら
[タイル利用ポリシー](https://operations.osmfoundation.org/policies/tiles/)に触れるので、
その時は別のタイル提供元に切り替える。

## 1. 公開版をビルドする

`USER` は自分のGitHubユーザー名、`shinten-radar` はリポジトリ名に置き換える。

```bash
cd C:\Users\take0\shinten-radar && python build_map.py --public --base-url "https://USER.github.io/shinten-radar"
```

`docs/index.html`（地図）と `docs/ogp.png`（SNSのサムネイル）が出る。
`--base-url` を省くとサムネイルが出ないので、必ず付ける。

## 2. GitHubにリポジトリを作る

ローカルのgitリポジトリは作成済み、初期コミットも済んでいる。

1. https://github.com/new を開く
2. Repository name に `shinten-radar`
3. **Public** を選ぶ（Privateだと無料プランではPagesが使えない）
4. README・.gitignore・license は**追加しない**（既にある）
5. 「Create repository」

## 3. push する

```bash
cd C:\Users\take0\shinten-radar && git remote add origin https://github.com/USER/shinten-radar.git && git push -u origin main
```

## 4. Pages を有効にする

1. リポジトリの **Settings** → 左メニューの **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / フォルダ: **/docs** → Save
4. 1〜2分待つと `https://USER.github.io/shinten-radar/` で公開される

## 5. 毎月の更新

```bash
cd C:\Users\take0\shinten-radar && python build_map.py --public --base-url "https://USER.github.io/shinten-radar" && git add docs && git commit -m "$(date +%Y-%m)月分を更新" && git push
```

`run_monthly.bat` に足しておけば、毎月16日の自動実行のあとに手でpushするだけになる。
push まで自動にすると、確認せずに公開されるのでおすすめしない。

## 公開されるもの・されないもの

| | git | 公開URL |
|---|---|---|
| `docs/index.html`（新店のみ） | ✅ | ✅ |
| スクリプト・README | ✅ | リポジトリ上で読める |
| `out/`（閉店・年表を含む手元の出力） | ❌ gitignore | ❌ |
| `data/` `masters/` `state/`（キャッシュ） | ❌ gitignore | ❌ |
| `.env`（LINEトークン） | ❌ gitignore | ❌ |
| `archive/ifas_data/`（21MBの旧CSV） | ❌ gitignore | ❌ |

リポジトリをPublicにするとスクリプトも読まれる。困るものは入っていないが、
気になるなら公開用に `docs/` だけの別リポジトリを作ってもよい。
