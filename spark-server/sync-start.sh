#!/usr/bin/env bash
# NVIDIA Sync の Custom Port から呼ぶ起動スクリプト。
# ゲートウェイを docker で起動する（イメージ名は適宜）。
set -euo pipefail
docker build -t vot-gateway .
docker run --rm -p 8000:8000 vot-gateway
