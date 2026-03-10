# Diagnostyka App - Reverse Engineering

Reverse engineering the Diagnostyka Android app to discover its API.

## Tools (installed in Docker container)

| Tool | Purpose |
|------|---------|
| **jadx** | Java/Android decompiler - decompiles DEX/APK bytecode back to Java source code |
| **apktool** | APK resource decoder - decodes AndroidManifest.xml, resources, and produces smali disassembly |
| **dex2jar** | Converts Dalvik DEX bytecode to standard Java JAR files for analysis |
| **jq** | Command-line JSON processor for parsing extracted API data |

## Structure

- `apk/` - Original XAPK file
- `output/` - Decompiled source and analysis results (generated)
