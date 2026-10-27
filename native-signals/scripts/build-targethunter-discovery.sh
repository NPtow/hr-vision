#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$task_root/.build/targethunter-discovery"
xcrun swiftc -parse-as-library -swift-version 5 -O \
  "$task_root/scripts/targethunter-discovery.swift" \
  "$task_root/Sources/SignalsCore/TargetHunterCredentialStore.swift" \
  -framework Foundation \
  -o "$task_root/.build/targethunter-discovery/targethunter-discovery"
echo "$task_root/.build/targethunter-discovery/targethunter-discovery"
