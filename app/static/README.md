# app/static

ブラウザに配る静的ファイル。`/static` で公開される（`app/main.py` の `app.mount`）。

**外部 CDN から読み込むものはここに取り込む。** 理由と方針は
[docs/adr/0010](../../docs/adr/0010-no-runtime-cdn.md)。

## 取り込み済みのライブラリ

| ファイル | 版 | 取得元 | ライセンス |
|---|---|---|---|
| `cytoscape.min.js` | 3.29.2 | `https://unpkg.com/cytoscape@3.29.2/dist/cytoscape.min.js` | MIT |

```
sha256  4d1ba05f57890d46e90ffb603e7d665bf9466e1d674e030f93d8e6e63b958587
```

## 更新するとき

版を上げたら、この表と sha256 も必ず直す。どの版が入っているかを
ファイルの中身から判別するのは現実的でないため、ここが唯一の記録になる。

```bash
V=3.29.2   # 上げたい版に変える
curl -fsSL -o app/static/cytoscape.min.js \
  "https://unpkg.com/cytoscape@${V}/dist/cytoscape.min.js"

# 版が本当に入れ替わったか確認する（ダウンロード成功だけでは確かめたことにならない）
grep -o 'version="[0-9.]*"' app/static/cytoscape.min.js
sha256sum app/static/cytoscape.min.js
```

そのあと `make e2e` を通すこと。グラフ描画は cytoscape に全面的に依存しているので、
壊れると E2E の `test_me_centered_graph_is_drawn` などが落ちる。
