#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGE_NAME="diagnostyka-reveng"
XAPK_FILE="apk/Diagnostyka - więcej niż wynik_2.0.7_APKPure.xapk"
OUTPUT_DIR="output"

echo "=== Step 1: Build Docker image ==="
docker build -t "$IMAGE_NAME" .

echo "=== Step 2: Extract XAPK ==="
mkdir -p "$OUTPUT_DIR/xapk-contents"
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  cd /workspace/$OUTPUT_DIR/xapk-contents
  unzip -o '/workspace/$XAPK_FILE'
"

echo "=== Step 3: Decompile main APK with jadx ==="
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  jadx --deobf --show-bad-code -d /workspace/$OUTPUT_DIR/jadx-output \
    /workspace/$OUTPUT_DIR/xapk-contents/pl.diagnostyka.mobile.apk
"

echo "=== Step 4: Extract native libraries from arm64 split APK ==="
mkdir -p "$OUTPUT_DIR/arm64-contents"
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  cd /workspace/$OUTPUT_DIR/arm64-contents
  unzip -o /workspace/$OUTPUT_DIR/xapk-contents/config.arm64_v8a.apk
"

echo "=== Step 5: Extract strings from Flutter binary (libapp.so) ==="
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  strings /workspace/$OUTPUT_DIR/arm64-contents/lib/arm64-v8a/libapp.so \
    > /workspace/$OUTPUT_DIR/libapp-strings.txt
"

echo "=== Step 6: Extract API endpoints and URLs ==="
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  cd /workspace/$OUTPUT_DIR

  echo '--- URLs found ---'
  grep -iE 'https?://' libapp-strings.txt | sort -u > urls-found.txt
  cat urls-found.txt

  echo ''
  echo '--- API endpoints ---'
  grep -E '^/api/' libapp-strings.txt | sort -u > api-endpoints.txt
  cat api-endpoints.txt

  echo ''
  echo '--- Backend hosts ---'
  grep -E 'mobile-fir-backend' libapp-strings.txt | sort -u > backend-hosts.txt
  cat backend-hosts.txt
"

echo ""
echo "=== Done! ==="
echo "Results in $OUTPUT_DIR/"
echo "  - jadx-output/     : Decompiled Java source"
echo "  - libapp-strings.txt : All strings from Flutter binary"
echo "  - urls-found.txt   : All URLs"
echo "  - api-endpoints.txt: API endpoint paths"
echo "  - backend-hosts.txt: Backend host URLs"
