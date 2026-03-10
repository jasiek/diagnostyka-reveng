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

echo "=== Step 7: Extract model/DTO field names ==="
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  cd /workspace/$OUTPUT_DIR

  echo '--- Model toString() patterns (reveal field names) ---'
  grep -oE '[A-Za-z]+(Dto|Response|Model|State|Args)\([^)]*' libapp-strings.txt | sort -u > model-fields.txt
  cat model-fields.txt

  echo ''
  echo '--- Enum values ---'
  grep -oE '[A-Z][a-zA-Z]+(Type|Status|Mode)\.[a-zA-Z]+' libapp-strings.txt | sort -u > enum-values.txt
  cat enum-values.txt

  echo ''
  echo '--- Auth/token strings ---'
  grep -iE '(bearer|authorization|x-api|access.token|refresh.token|idToken)' libapp-strings.txt | sort -u > auth-strings.txt
  cat auth-strings.txt
"

echo "=== Step 8: Extract AndroidManifest and network config ==="
docker run --rm -v "$SCRIPT_DIR:/workspace" "$IMAGE_NAME" bash -c "
  cd /workspace/$OUTPUT_DIR

  echo '--- Network Security Config ---'
  cat jadx-output/resources/res/xml/network_security_config.xml 2>/dev/null || echo 'Not found'

  echo ''
  echo '--- Key string resources ---'
  grep -E 'redirect_domain|facebook_app_id|facebook_client_token' \
    jadx-output/resources/res/values/strings.xml 2>/dev/null || echo 'Not found'

  echo ''
  echo '--- SSL certificates ---'
  find jadx-output/resources/assets/ -name '*.cert' -o -name '*.pem' -o -name '*.crt' 2>/dev/null
"

echo ""
echo "=== Done! ==="
echo "Results in $OUTPUT_DIR/"
echo "  - jadx-output/      : Decompiled Java source"
echo "  - libapp-strings.txt: All strings from Flutter binary"
echo "  - urls-found.txt    : All URLs"
echo "  - api-endpoints.txt : API endpoint paths"
echo "  - backend-hosts.txt : Backend host URLs"
echo "  - model-fields.txt  : Model/DTO field names"
echo "  - enum-values.txt   : Enum values"
echo "  - auth-strings.txt  : Auth-related strings"
echo ""
echo "API spec: api-spec.yaml"
