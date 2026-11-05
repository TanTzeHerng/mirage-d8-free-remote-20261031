#!/usr/bin/env bash
set -euo pipefail
# Ubuntu 24.04 Actions no longer exposes a usable 'lc0' apt package.
if [ -x "$GITHUB_WORKSPACE/engine_bin/lc0" ]; then
  echo "CACHED_LC0_CPU_ALREADY_INSTALLED"
  exit 0
fi
# Build the upstream pinned v0.32.1 with the CPU/OpenBLAS backend.
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  git build-essential pkg-config meson ninja-build zlib1g-dev \
  libopenblas-dev libprotobuf-dev protobuf-compiler libgomp1
mkdir -p engine_bin
git clone --depth 1 --branch v0.32.1 --recurse-submodules --shallow-submodules \
  https://github.com/LeelaChessZero/lc0.git /tmp/beast-lc0-src
cd /tmp/beast-lc0-src
./build.sh -Dblas=true -Dopenblas=true -Dispc=false -Dgtest=false \
  -Dplain_cuda=false -Dcudnn=false -Dopencl=false -Donnx=false \
  -Daccelerate=false -Dnative_arch=true
cp build/release/lc0 "$GITHUB_WORKSPACE/engine_bin/lc0"
chmod +x "$GITHUB_WORKSPACE/engine_bin/lc0"
"$GITHUB_WORKSPACE/engine_bin/lc0" --version || true
echo LC0_CPU_OPENBLAS_READY
