# ハンドオーバー: ブラウザ動画 多言語→日本語 リアルタイム・オーバーレイ翻訳

> 設計フェーズの確定事項。実装は Claude Code for VSCode で進める。
> 環境固有の実値（実機名・IP・tailnet 名）は gitignore 済みの `CLAUDE.local.md` にあり、本書には書かない。

---

## 1. 目的と著作権スタンス（最重要・設計の核）

任意サイトで再生中の多言語動画の音声を、ローカルで日本語に翻訳して画面に重ねて見る。

- 和訳字幕を動画に焼き込んで新しい動画ファイルを生成しない（翻案物の生成にあたるため）。
- 方式は再生時のリアルタイム・オーバーレイ。元動画に手を加えず、和訳は透明レイヤーで一時表示するのみ。保存しない。
- 字幕は「数秒遅れて流れるライブ字幕」。暫定（薄字）→確定（濃字）の2段で出すと体感が良い。

※ 法的判断は専門家の領分。ここでの整理は設計方針であって最終的な可否判断ではない。

---

## 2. 全体アーキテクチャ

重い処理はすべて the Spark に置き、the Mac client は「音を拾う」と「字幕を重ねる」だけに徹する。

```
[the Mac client / Chrome]                       [the Spark]
  拡張機能                          Tailscale       推論ゲートウェイ
  ├ service worker / offscreen      (mesh)          ├ FastAPI + WSS
  │  ├ tabCapture で音声取得   ───────────────────→  ├ faster-whisper (CUDA)  原語STT
  │  └ Opus圧縮して WSS送信                          └ Qwen系LLM (vLLM)       →和訳
  └ content script                  ←───────────────   和訳テキストを返す
     透明オーバーレイに描画          (テキストのみ)
       自宅 = 同じ名前 / 外出(テザリング) = 同じ名前
```

動画はブラウザ内でローカル再生されたまま、どこにも送られない。
Spark に上るのは音声のみ（Opus 16〜24kbps）、返るのは和訳テキストのみ。

---

## 3. デバイスとデータ経路（Tailscale で一本化）

- ランタイムの音声↔テキスト往復は Tailscale 経由。両機が固定アドレスを持ち、同一LANでなくても到達できる。
  → 自宅Wi-Fiでも外出先テザリングでも同じエンドポイントで動く（切り替え不要）。
- SSH / NVIDIA Sync は開発時のターミナル・ファイル同期・VSCode リモート接続用に残す。
- エンドポイントは IP 直打ちではなく MagicDNS 名で一本化。
- **実機名・IP・tailnet 名は `CLAUDE.local.md` を参照**（本書・コミット対象には書かない）。

---

## 4. ネットワークの落とし穴と回避策（実装前に必ず潰す）

https ページの content script から `http://100.x` を直接 fetch すると mixed content でブロックされ、
私的アドレスのため PNA プリフライトにも引っかかりうる。回避は2層:

1. **ネットワーク処理を service worker / offscreen に集約**（必須）。tabCapture も WSS もここに置き、
   content script は描画専任にする。
2. **Tailscale で正規の HTTPS 証明書**（本番の本命）。`tailscale cert` / `tailscale serve` で
   MagicDNS 名に Let's Encrypt 証明書を発行し、`wss://...ts.net` を secure context にする。

---

## 5. モデル構成（すべて Spark 上・ローカル完結）

128GB ユニファイドメモリにより「ローカルなのに高品質」が成立。

- STT: faster-whisper large-v3（CUDA）。
- 翻訳: Qwen2.5/3 系 Instruct を vLLM で常駐。LLM の方が日本語の流暢さで専用翻訳モデルより上。
- STT → 翻訳の2段構成（Whisper の translate は英訳専用で和訳に使えない）。

---

## 6. リポジトリ構成（モノレポ）

```
video-overlay-translator/
├ CLAUDE.md            # コミット用ガード（秘匿値なし）
├ CLAUDE.local.md      # gitignore 済み・環境固有の実値
├ HANDOVER.md          # 本書
├ .env.example         # プレースホルダ
├ extension/           # MV3 拡張（Mac ローカルの VSCode で開発）
└ spark-server/        # FastAPI ゲートウェイ（Spark に Remote-SSH して開発・実行）
```

---

## 7. 開発ワークフロー（VSCode 2ウィンドウ運用）

- `extension/` は Mac ローカルの VSCode で（Chrome が Mac 上にあるため）。
- `spark-server/` は VSCode Remote-SSH で Spark に接続したウィンドウで編集・実行（CUDA を直接触る）。
  NVIDIA Sync がリモート接続を構成。Custom Port に `sync-start.sh` を紐づけてゲートウェイを起動。
- → ローカル（拡張）とリモート（サーバ）の2つの VSCode ウィンドウを行き来する。

---

## 8. 最初のマイルストーン（疎通優先）

1. `spark-server` に WSS エコーサーバ（音声を受けて固定文字列を返すだけ）を立てる。
2. 最初から Tailscale 名をターゲットにする（`localhost` 経由にしない）。
3. Mac の拡張から、自宅Wi-Fi と スマホテザリングの両方で叩けることを確認。
4. 通ったら faster-whisper → vLLM を順に差し込む。

---

## 9. 依存アップデート監視の方針

`.github/dependabot.yml` で **pip（`spark-server/requirements.txt`）と docker（Dockerfile ベースイメージ）** を監視する。
意図的に **自動監視の対象外**にしているものがあり、これらは自動化せず手動で扱う:

- **CUDA 版 torch**（`torch==2.x+cuXXX`）: PyPI ではなく PyTorch の CUDA 専用 index 配布で、ローカルバージョン
  `+cuXXX` を Dependabot が正しく比較できない。Blackwell ARM64 は torch/CUDA/driver の整合が崩れると動かないため、
  自動 bump させない。更新は [PyTorch releases] を見て手動で `--index-url` を差し替える。
- **モデル重み（Whisper / LLM）**: `openai/whisper-large-v3` 等は HF Hub の成果物で semver パッケージではなく、
  Dependabot/pip では追跡不可。更新は `.env` の `WHISPER_MODEL` / `VLLM_MODEL` を差し替える設計判断で行う。
- **Whisper の実行系そのものは `transformers` 経由**なので、pip 監視（`transformers` の pin）で既にカバー済み。
  別途の Whisper 監視は不要。

CUDA ベースイメージへ差し替えた時点で、その base image は docker エコシステムが自動で拾う。

---

## 10. 未決定事項（実装しながら詰める）

- 翻訳 LLM: Qwen 以外の候補。サービング基盤は vLLM か NIM か（DGX Spark は `-dgx-spark` 変種コンテナが必要）。
- Whisper の常駐方法（プロセス常駐 / コンテナ / バッチ境界の切り方）。
- 字幕の暫定→確定の確定タイミング（VAD 区切りか時間窓か）。
- `tailscale serve` での WSS 終端の具体構成。
