#!/usr/bin/env bash
# All publication now runs from committed source through GitHub Actions.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
branch="$(git branch --show-current)"
case "$branch" in main|codex/mockups) ;; *) echo 'Switch to main or codex/mockups to publish.' >&2; exit 1;; esac
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo 'Commit and push your changes first. This command deploys the remote branch.' >&2
  exit 1
fi
remote_sha="$(git ls-remote origin "refs/heads/$branch" | cut -f1)"
if [ "$(git rev-parse HEAD)" != "$remote_sha" ]; then
  echo 'Push this commit before publishing.' >&2
  exit 1
fi
gh workflow run deploy.yml --repo NPtow/hr-vision --ref "$branch"
printf 'Workflow requested for %s: https://github.com/NPtow/hr-vision/actions/workflows/deploy.yml\n' "$branch"
