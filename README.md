# video-overlay-translator

ブラウザで再生中の多言語動画の音声を、ローカル GPU（NVIDIA DGX Spark）でリアルタイムに
日本語へ翻訳し、動画の上に透明オーバーレイで字幕として重ねて表示する Chrome 拡張 +
推論ゲートウェイ。元動画には一切手を加えず、和訳はどこにも保存しない（再生時オーバーレイのみ）。

## 構成

- `extension/` — Manifest V3 拡張。タブ音声を取得し、和訳を透明レイヤーに描画する。
- `spark-server/` — Spark 上で動く FastAPI ゲートウェイ。STT（faster-whisper）→ 翻訳（LLM/vLLM）。

通信は Tailscale メッシュ経由。音声（上り）と和訳テキスト（下り）だけが流れ、動画は流れない。

## 開発状況

初期開発中。安定・汎用化したら公開予定。設計の全体像は `HANDOVER.md`、
Claude Code 向けの規約は `CLAUDE.md` を参照。

## セットアップ（概要）

1. `cp .env.example .env` し、Spark の WSS URL を実値で埋める（`.env` はコミットされない）。
2. `spark-server/` を Spark 上で起動（まずは WSS エコーで疎通確認）。
3. `extension/` を Chrome に読み込み、自宅Wi-Fi とテザリングの両方で疎通を確認。

## ライセンス

[MIT License](LICENSE)。
