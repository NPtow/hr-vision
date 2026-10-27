#!/usr/bin/env bash
# Root-owned command forced by the dedicated GitHub deploy keys on dsa.
# It can only stream a release to the HR Vision receiver on outreach.
set -euo pipefail
case "${1:-}" in service|mockups) target="$1";; *) exit 64;; esac
exec timeout 480 /usr/bin/ssh -T \
  -i "/var/lib/hr-vision-relay/$target" \
  -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/var/lib/hr-vision-relay/known_hosts \
  -o ConnectTimeout=20 hr-vision-deploy@10.130.0.18 publish
