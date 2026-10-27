#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
task_build="$task_root/.build/release"
if [[ ! -f "$task_build/Modules/SignalsCore.swiftmodule" ]]; then
  echo 'Build SignalsCore first: swift build -c release --target SignalsCore' >&2
  exit 1
fi
mkdir -p "$task_root/.build/serper-refine"
xcrun swiftc -parse-as-library -swift-version 5 -O \
  -I "$task_build/Modules" \
  "$task_root/scripts/serper-refine.swift" \
  "$task_build"/SignalsCore.build/*.o \
  -framework Foundation -framework Security \
  -o "$task_root/.build/serper-refine/serper-refine"
echo "$task_root/.build/serper-refine/serper-refine"
