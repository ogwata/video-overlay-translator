# スライド（技術概要）

このリポジトリの設計・アーキテクチャを数枚にまとめた技術者向けスライド。

- `overview.pdf` — 配布用 PDF（6 ページ・16:9 / 1920×1080）。
- `overview.html` — 編集元の自己完結 HTML（外部依存なし。macOS 標準フォントで描画）。
- `overview-Express-export.html` — Adobe Express 取り込み用 HTML（フォントを Adobe Fonts の M+ に移行し、色・地色を明示）。

## Adobe Express（編集可能なドキュメント）

上記 `overview-Express-export.html` を取り込んだネイティブ Express ドキュメント:

- https://new.express.adobe.com/id/urn:aaid:sc:AP:830e7247-2078-4c50-baf2-4afbd264b6e2

再生成は `overview-Express-export.html` を編集 →（Adobe for Creativity 連携の）`export_html_to_express` で再取り込み。

## 再生成（HTML → PDF）

`overview.html` を編集後、Chrome ヘッドレスで PDF を書き出す:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --user-data-dir=/tmp/vot-chrome \
  --print-to-pdf=docs/slides/overview.pdf \
  "file://$PWD/docs/slides/overview.html"
```
