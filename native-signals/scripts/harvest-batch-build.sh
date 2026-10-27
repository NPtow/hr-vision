#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
task_build="$task_root/.build/release"
mkdir -p "$task_root/.build/harvest-batch"
xcrun swiftc -parse-as-library -swift-version 5 -O \
  -I "$task_build/Modules" "$task_root/scripts/harvest-batch.swift" \
  "$task_build"/SignalsCore.build/*.o -framework Foundation -framework Security \
  -o "$task_root/.build/harvest-batch/harvest-batch"
echo "$task_root/.build/harvest-batch/harvest-batch"
