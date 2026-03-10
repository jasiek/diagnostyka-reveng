# Diagnostyka App - Reverse Engineering

Reverse engineering the Diagnostyka Android app (v2.0.7) to discover its API.

## Quick Start

```bash
./run.sh
```

This builds the Docker container, extracts the XAPK, decompiles with jadx, and extracts API strings from the Flutter binary.

## Key Findings

- **App type:** Flutter (Dart compiled to `libapp.so`)
- **Package:** `pl.diagnostyka.mobile`
- **Backend:** `https://mobile-fir-backend.diag.pl`
- **Auth:** Firebase Auth (phone + SMS OTP) → Bearer token
- **HTTP client:** Dio
- **Real-time:** Socket.IO
- **Payment:** PayU + TWISTO (buy now pay later)
- **Identity verification:** mObywatel, MojeID (KIR)

## API Specification

See [`api-spec.yaml`](api-spec.yaml) for the full OpenAPI 3.0 specification with 40+ endpoints.

## Tools (installed in Docker container)

| Tool | Purpose |
|------|---------|
| **jadx** | Java/Android decompiler - decompiles DEX/APK bytecode back to Java source code |
| **apktool** | APK resource decoder - decodes AndroidManifest.xml, resources, and produces smali disassembly |
| **dex2jar** | Converts Dalvik DEX bytecode to standard Java JAR files for analysis |
| **jq** | Command-line JSON processor for parsing extracted API data |
| **strings** | Extracts printable strings from the compiled Flutter binary (libapp.so) |
| **blutter** | Dart AOT snapshot analyzer - extracts class definitions, method signatures from Flutter libapp.so |

## Structure

- `apk/` - Original XAPK file
- `run.sh` - Reproducible extraction script
- `api-spec.yaml` - OpenAPI 3.0 specification (reverse-engineered)
- `output/` - Decompiled source and analysis results (generated, gitignored)
  - `jadx-output/` - Decompiled Java source (Android wrapper)
  - `libapp-strings.txt` - All strings from Flutter binary
  - `api-endpoints.txt` - Extracted API endpoint paths
  - `urls-found.txt` - All URLs found in the binary
  - `backend-hosts.txt` - Backend host URLs
