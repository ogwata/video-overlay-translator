# CLAUDE.md

ブラウザ動画の多言語音声を、ローカルの GPU マシン（the Spark）でリアルタイムに日本語へ翻訳し、
再生中の動画に透明オーバーレイで重ねて表示する Chrome 拡張 + 推論ゲートウェイ。

> 設計の全体像と根拠は @HANDOVER.md を参照。
> 自分の環境固有値（実機名・IP・tailnet 名）は gitignore 済みの CLAUDE.local.md にあり、
> Claude Code 起動時に本ファイルと一緒に読み込まれる。**本ファイルには秘匿値を書かない。**

---

## 厳守事項（ガード）

1. **役割分担を崩さない**
   the Mac client = 音声取得 + オーバーレイ描画 / the Spark = STT + 翻訳の推論。
   重い処理は必ず Spark 側。Mac 側に推論を持ち込まない。

2. **エンドポイントは Tailscale MagicDNS 名で一本化**
   自宅/外出で切り替えない。IP 直打ちしない。実値は `.env` / CLAUDE.local.md から読む。
   コードとマニフェストでは `*.ts.net` のようなワイルドカード/プレースホルダで表現する。

3. **拡張のネットワーク処理は service worker / offscreen に集約**
   content script はオーバーレイ描画専任。サーバを直接叩かない
   （mixed content / Private Network Access を回避するため）。

4. **元動画には触れない。再生時オーバーレイのみ**
   字幕の焼き込み・動画ファイルの生成は禁止（著作権上の核）。
   字幕は「数秒遅れて流れるライブ字幕」。暫定（薄字）→確定（濃字）の2段で出す。

5. **公開方針: 初期はプライベート、安定・汎用化後に Public へ切り替える前提**
   公開可能になったら GitHub の可視性を Private → Public に変更する。

6. **秘匿値・環境固有値は実値をコミットしない**
   Tailscale MagicDNS 名 / `100.x` IP / 実機ホスト名 / 証明書・鍵 (`*.key` `*.pem` `*.crt`) /
   各種キーは `.env` と `*.example`（プレースホルダ）、または CLAUDE.local.md で扱う。
   **可視性を切り替えてもコミット履歴は引き継がれる**ため、プライベート期間も同基準で徹底する。
   `.gitignore` と `*.example` は**最初のコミットに含める**。

---

## アーキテクチャ（要約）

```
[the Mac client / Chrome 拡張]              [the Spark]
 service worker / offscreen   Tailscale     FastAPI + WSS
  ├ tabCapture で音声取得   ───(mesh)────→   ├ faster-whisper (CUDA)  原語STT
  └ Opus圧縮して WSS送信                      └ Qwen系LLM (vLLM)       →和訳
 content script            ←──(テキスト)──    和訳テキストを返す
  透明オーバーレイ描画
```

回線に流れるのは音声（上り、Opus 16〜24kbps）と和訳テキスト（下り）のみ。
動画はブラウザ内ローカル再生のまま、どこにも送らない。
翻訳は **STT → 翻訳の2段**（Whisper の translate は英訳専用で和訳に使えないため）。

---

## リポジトリ構成

```
extension/        # MV3 拡張（the Mac client のローカル VSCode で開発）
  src/service-worker.js  #  tabCapture 調整 + メッセージング
  src/offscreen.*        #  音声キャプチャ + WSS 通信を集約
  src/content-script.js  #  オーバーレイ描画のみ
  src/overlay.css
spark-server/     # FastAPI ゲートウェイ（the Spark に Remote-SSH して開発・実行）
  main.py / requirements.txt / Dockerfile / sync-start.sh
```

---

## 開発ワークフロー

- `extension/` は **the Mac client のローカル VSCode** で（Chrome が Mac 上にあるため）。
- `spark-server/` は **VSCode Remote-SSH で the Spark に接続したウィンドウ**で編集・実行（CUDA 環境を直接触る）。
  NVIDIA Sync がリモート接続を構成する。Custom Port に `sync-start.sh` を紐づけてゲートウェイを起動。
- → ローカル（拡張）とリモート（サーバ）の **2つの VSCode ウィンドウ**を行き来する。

---

## 最初のマイルストーン

推論を差し込む前に経路を通す。

1. `spark-server` の WSS エコーサーバ（音声を受けて固定の和訳文字列を返すだけ）を起動。
2. 最初から Tailscale 名（CLAUDE.local.md の `SPARK_WSS_URL`）をターゲットにする。`localhost` 経由にしない。
3. Mac の拡張から、**自宅Wi-Fi と スマホテザリングの両方**で叩けることを確認。
4. 通ったら faster-whisper → vLLM を順に差し込む。

## コーディング上の約束

- 変更は最小限に。関係ないコードのリファクタはしない。
- 論理単位ごとにコミットを分ける。巨大な一括コミットにしない。
- 2案で迷ったら両方提示して選ばせる。アーキテクチャの独断決定をしない。
