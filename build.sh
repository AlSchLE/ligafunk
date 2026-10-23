#!/usr/bin/env bash
# Render build script: install Python dependencies and fetch Litestream.
set -euo pipefail

pip install -r requirements.txt

mkdir -p bin
curl -fsSL \
  -o /tmp/litestream.tar.gz \
  https://github.com/benbjohnson/litestream/releases/download/v0.5.17/litestream-0.5.17-linux-x86_64.tar.gz
echo "cfb371176d164437ae869f8351cfde49bd1804ae71c61923f75c9cba9c9c006d  /tmp/litestream.tar.gz" | sha256sum -c -
tar -xzf /tmp/litestream.tar.gz -C bin litestream
chmod +x bin/litestream
