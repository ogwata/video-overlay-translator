# スライド（技術概要）

このリポジトリの設計・アーキテクチャを数枚にまとめた技術者向けスライド。

- `overview.pdf` — 配布用 PDF（6 ページ・16:9 / 1920×1080）。
- `overview.html` — 編集元の自己完結 HTML（外部依存なし。macOS 標準フォントで描画）。

## 再生成（HTML → PDF）

`overview.html` を編集後、Chrome ヘッドレスで PDF を書き出す:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --user-data-dir=/tmp/vot-chrome \
  --print-to-pdf=docs/slides/overview.pdf \
  "file://$PWD/docs/slides/overview.html"
```
