#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$task_root/.build/targethunter-pilot"
xcrun swiftc -parse-as-library -swift-version 5 -O \
  "$task_root/scripts/targethunter-pilot.swift" \
  "$task_root/Sources/SignalsCore/TargetHunterCredentialStore.swift" \
  -framework Foundation \
  -o "$task_root/.build/targethunter-pilot/targethunter-pilot"
echo "$task_root/.build/targethunter-pilot/targethunter-pilot"
