#!/usr/bin/env bash
set -euo pipefail

# Reproducible, standalone Qwen Android build inputs.
# llama.cpp source is compiled into JNI libraries by Gradle/CMake.
# Qwen GGUF is embedded as an APK asset; no network is needed on the phone.
readonly LLAMA_REV="969d7333172c522d6a60fffb8dd35288ce8df236"
readonly QWEN_REV="1208e45d782fe18602c5eaf10e5758d5b0f24c03"
readonly MODEL_NAME="Qwen3-0.6B-Q4_K_M.gguf"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLAMA="$ROOT/third_party/llama.cpp"
MODEL="$ROOT/app/src/main/assets/$MODEL_NAME"

if [[ ! -f "$LLAMA/CMakeLists.txt" ]]; then
  mkdir -p "$LLAMA"
  git -C "$LLAMA" init -q
  git -C "$LLAMA" remote add origin https://github.com/ggml-org/llama.cpp.git
  git -C "$LLAMA" fetch --depth 1 origin "$LLAMA_REV"
  git -C "$LLAMA" -c advice.detachedHead=false checkout --detach FETCH_HEAD
fi
actual="$(git -C "$LLAMA" rev-parse HEAD)"
[[ "$actual" == "$LLAMA_REV" ]] || { echo "Wrong llama.cpp revision: $actual"; exit 1; }

mkdir -p "$(dirname "$MODEL")"
if [[ ! -f "$MODEL" ]]; then
  curl --fail --location --retry 4 --retry-delay 5 --connect-timeout 40 \
    --output "$MODEL.part" \
    "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/$QWEN_REV/$MODEL_NAME"
  mv "$MODEL.part" "$MODEL"
fi
size="$(wc -c < "$MODEL")"
[[ "$size" -gt 350000000 ]] || { echo "Qwen model too small: $size bytes"; exit 1; }
[[ "$(head -c 4 "$MODEL")" == "GGUF" ]] || { echo "Invalid Qwen GGUF file"; exit 1; }
echo "Bundling genuine Qwen3 0.6B GGUF ($size bytes) and llama.cpp $LLAMA_REV"
