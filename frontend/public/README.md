# frontend/public

Next.js が静的に配る資産。

## cytoscape.min.js

`/debug`（既存画面、`debug.html`）が使う。**外部 CDN から読み込まない**方針
（[docs/adr/0010](../../docs/adr/0010-no-runtime-cdn.md)）で、`app/static/` にあったものを
そのまま移設した（backend からは `/` `/debug` を削除したため）。

| ファイル | 版 | 取得元 | ライセンス |
|---|---|---|---|
| `cytoscape.min.js` | 3.29.2 | `https://unpkg.com/cytoscape@3.29.2/dist/cytoscape.min.js` | MIT |

```
sha256  4d1ba05f57890d46e90ffb603e7d665bf9466e1d674e030f93d8e6e63b958587
```

版を上げるときの手順は移設前と同じ（`curl` で取得 → 版とハッシュをこの表に書き戻す →
`make e2e` を通す。グラフ描画は cytoscape に全面的に依存している）。

```bash
V=3.29.2   # 上げたい版に変える
curl -fsSL -o frontend/public/cytoscape.min.js \
  "https://unpkg.com/cytoscape@${V}/dist/cytoscape.min.js"
grep -o 'version="[0-9.]*"' frontend/public/cytoscape.min.js
sha256sum frontend/public/cytoscape.min.js
```
