#!/usr/bin/env bash
# API and its UI are released together from main.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ "$(git -C "$root" branch --show-current)" != main ]; then
  echo 'The API is published only from main.' >&2
  exit 1
fi
exec bash "$root/interface-lab/scripts/deploy-yc.sh"
