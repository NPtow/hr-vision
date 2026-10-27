#!/bin/bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
swift build -c release
TASK_BIN="$(swift build -c release --show-bin-path)"
TASK_APP="$TASK_ROOT/dist/TG Рассылщик.app"
mkdir -p "$TASK_APP/Contents/MacOS" "$TASK_APP/Contents/Resources"
cp "$TASK_BIN/TGSignals" "$TASK_APP/Contents/MacOS/TGSignals"
TASK_RESOURCE_BUNDLE="$TASK_BIN/TGSignals_SignalsCore.bundle"
if [[ ! -f "$TASK_RESOURCE_BUNDLE/discovered-companies.json" && ! -f "$TASK_RESOURCE_BUNDLE/Contents/Resources/discovered-companies.json" ]]; then
  printf 'Missing company catalog in %s\n' "$TASK_RESOURCE_BUNDLE" >&2
  exit 1
fi
ditto "$TASK_RESOURCE_BUNDLE" "$TASK_APP/Contents/Resources/TGSignals_SignalsCore.bundle"
ditto "$TASK_BIN/TGSignals_SignalsApp.bundle" "$TASK_APP/Contents/Resources/TGSignals_SignalsApp.bundle"
mkdir -p "$TASK_APP/Contents/Resources/HistoryCollectors"
cp "$TASK_ROOT/scripts/school-vk-history.py" "$TASK_ROOT/scripts/school-hiring-history.py" "$TASK_APP/Contents/Resources/HistoryCollectors/"
if [[ ! -f "$TASK_ROOT/rpa/telemetr/node_modules/playwright-core/package.json" ]]; then
  printf 'Install RPA dependencies with: cd rpa/telemetr && npm ci --ignore-scripts\n' >&2
  exit 1
fi
ditto "$TASK_ROOT/rpa/telemetr" "$TASK_APP/Contents/Resources/telemetr-rpa"
swift scripts/make-icon.swift "$TASK_ROOT/dist/Signals.iconset"
iconutil -c icns "$TASK_ROOT/dist/Signals.iconset" -o "$TASK_APP/Contents/Resources/Signals.icns"
cat > "$TASK_APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.hrvision.tg-signals</string>
<key>CFBundleName</key><string>TG Рассылщик</string>
<key>CFBundleDisplayName</key><string>TG Рассылщик</string>
<key>CFBundleExecutable</key><string>TGSignals</string>
<key>CFBundleIconFile</key><string>Signals</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.11.1</string>
<key>CFBundleVersion</key><string>17</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSPrincipalClass</key><string>NSApplication</string>
<key>LSApplicationCategoryType</key><string>public.app-category.business</string>
<key>CFBundleLocalizations</key><array><string>ru</string><string>en</string></array>
</dict></plist>
PLIST
codesign --force --sign - "$TASK_APP"
printf '%s\n' "$TASK_APP"
