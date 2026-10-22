---
name: spark-ml-stack
description: "ML スタックを Spark (DGX Spark = GB10, Grace ARM64 + Blackwell sm_121) にインストールするときの確定事項。aarch64 + 最新コンピュート能力固有の地雷を含む。"
metadata: 
  node_type: memory
  type: project
  originSessionId: a22a3a9c-0ec1-4e07-ba02-131132db0705
---

DGX Spark (CLAUDE.local.md の edgexpert-b0a9) は **Grace CPU = ARM64 (aarch64)** + **Blackwell GPU = NVIDIA GB10, compute capability sm_121**。

ここに ML 系の Python パッケージを入れるときに踏みやすい地雷:

- **PyPI の `ctranslate2` aarch64 wheel は CPU 専用**。CUDA 同梱の wheel は x86_64 にしか配布されていない。faster-whisper は CT2 を引くので、`device='cuda'` だと "This CTranslate2 package was not compiled with CUDA support" で落ちる。
  → 回避: transformers + PyTorch ASR パイプライン経由で Whisper を回す（[[main-py-stt-arch]] 参照）。

- **PyTorch の cuXXX index は sm_121 をカバーするバージョンが限られる**。`cu126` の wheel は sm_80/sm_90 までしかコンパイルされていない（Ampere + Hopper）。GB10 (sm_121) を引くには **`cu130` 以上**が必要。
  ```bash
  .venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu130
  ```
  cu130 でも sm_121 が無い場合は nightly: `https://download.pytorch.org/whl/nightly/cu130`。

- 「PyTorch was built for CCs ... 8.0, 9.0」のような **UserWarning** が出る間は GPU カーネルが見つからないので `cudaErrorNoKernelImageForDevice` で実行時失敗する。警告が消える wheel を選ぶ。

- 動作確認用ワンライナー:
  ```bash
  .venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_capability(0))"
  # 期待: True (12, 1)  かつ警告なし
  ```

**Why:** 2026-05-23 のセッションで faster-whisper → CT2 → CUDA エラー、続いて transformers + cu126 → sm_80/90 限定で `cudaErrorNoKernelImageForDevice` を実際に踏み、cu130 まで上げて解決した経緯。

**How to apply:** 次に Spark へ ML ランタイム（vLLM, NeMo, TensorRT-LLM 等）を入れるときは、aarch64 wheel が CUDA を持っているか + Blackwell コンピュート能力をカバーしているかを最初に確認する。CT2 系（faster-whisper, opennmt-py 等）は同じ理由で避けるか source build する。
