# Automatic vacancy comparison: ordered code release

This procedure ships universal automation code. It does not inspect candidate or
vacancy payloads, run manual comparisons, call providers, or recalculate existing
production rows. The new lifecycle owns ordinary automatic work after startup.
Use only the full commit explicitly reviewed by the release owner.

## Order and compatibility

1. Commit the complete implementation and tests; root owns PR/merge. The release
   owner supplies `REVIEWED_CODE_SHA`, a full 40-hex immutable reviewed commit.
   Recommendations may be staged from this reviewed branch commit before merge;
   its exporter and guarded updater have no main-reachability requirement.
2. Export **recommendations only** from that exact immutable commit. Runtime 1.2.0
   adds `tanh-higher-zero-v3` (unknown importance has effective weight zero),
   accepts `tanh-v1` and `tanh-higher-v2`, and returns historical
   saved results unchanged. SQLite schema and `recommendations-v1` data compatibility
   remain unchanged.
3. Upgrade recommendations using `guarded_upgrade.py --service recommendations`.
   Existing HR uses its prior explicit policy during this interval. The wrapper
   holds HR/material writers paused through service health and original HR/origin
   checks. Other assessment units are untouched.
4. Verify the installed receiver against the reviewed source. If it already matches,
   retain it; this policy-only release requires no receiver change. Otherwise,
   before new HR delivery, root separately installs the reviewed receiver from the
   reviewed HR release commit at `/usr/local/libexec/hr-vision-ci/receiver.py`. The complete runtime
   adds `recommendation_lifecycle.py`; previous complete layouts remain accepted.
   The CI payload cannot update its own receiver.
5. Protected CI on main builds HR API plus exactly three service UI stories from
   the reviewed commit. The packager explicitly includes the new module; receiver
   validates inventory/hash, branch head, live meetings, backup and compatible
   activation. No manual HR restart.

Main pushes trigger CI automatically. Keep the protected `service` environment
gate pending until recommendations and receiver prerequisites pass, or arrange
these prerequisites before merging the PR. A delayed workflow fails closed if
main advances; review the newer exact commit before retrying.

## Commands with reviewed observed identities

Templates below are not authorization to execute them. Replace uppercase values
with observed reviewed identifiers. Build locally after the reviewed code commit exists (it may precede the HR merge):

```sh
python3 -B infra/deployment/services/assessment-services/package.py recommendations --commit REVIEWED_CODE_SHA --output /tmp/recommendations-REVIEWED_CODE_SHA.tar.gz
```

Transfer reviewed artifact/helpers through the approved administrative channel.
On the target root shell, without credential arguments:

```sh
python3 -B /root/assessment-delivery/operate.py recommendations inspect --validate-current --probe
python3 -B /root/assessment-delivery/guarded_upgrade.py /root/recommendations-REVIEWED_CODE_SHA.tar.gz --service recommendations --expected-current OBSERVED_RECOMMENDATIONS_CURRENT_SHA --receiver /usr/local/libexec/hr-vision-ci/receiver.py --receiver-sha256 REVIEWED_INSTALLED_RECEIVER_SHA256
python3 -B /root/assessment-delivery/operate.py recommendations inspect --validate-current --probe
```

The first guarded upgrade uses the actually installed receiver and its reviewed
digest; it may still be the previous compatible receiver. Root then installs the
new reviewed receiver before releasing the protected HR workflow. Preserve previous
receiver/helper bytes privately. Never relax file guards or delete state to activate.

After the reviewed SHA becomes main, observe its automatically triggered run:

```sh
gh run list --repo NPtow/hr-vision --workflow deploy.yml --commit REVIEWED_MAIN_SHA
gh run watch OBSERVED_RUN_ID --repo NPtow/hr-vision --exit-status
```

If root explicitly chooses a retry and main still equals the reviewed commit:
`gh workflow run deploy.yml --repo NPtow/hr-vision --ref main`. Verify the resulting
run's `headSha` before approving deployment. Successful CI differs from acceptance:
compare deployment SHA and bounded health/readiness through read-only host checks.
Do not query candidate/vacancy payloads or POST recalculations as smoke checks.

## Atomic reviewed receiver installation

The receiver change is a separate root-approved operation after recommendations
is healthy. Export the receiver bytes from the exact reviewed HR commit, transfer
that source and `install_receiver.py` through the approved channel, and provide both
observed old and reviewed new SHA256 values. The installer acquires the exact two
receiver deployment locks nonblocking, requires root-owned protected regular files,
checks Python syntax without importing, writes private old bytes/mode/hash/commit
metadata under `/var/backups/hr-vision-ci/receiver/`, rechecks the old bytes, then
atomically replaces and verifies mode-0644 receiver. It never restarts services.

```sh
python3 -B /root/assessment-delivery/install_receiver.py /root/assessment-delivery/receiver-REVIEWED_HR_SHA.py --expected-old-sha256 b5d078f0e427df7c14d61c0d0f9aec9c424efd6dbd1536149ebedd957ff707b6 --expected-new-sha256 REVIEWED_NEW_RECEIVER_SHA256 --reviewed-commit REVIEWED_HR_SHA
```

The old hash above was observed before the recommendations deployment on
26.10.2026; recheck it immediately before receiver installation. Changed old bytes
fail closed. The reviewed commit field is receipt provenance; the separate expected
new hash pins the exported bytes. This installation has not been executed by the
preparation script/tests.

## Recovery boundaries

Failed recommendations health automatically returns old compatible service/config
and retains live DB; guarded wrapper restores original HR. Once new HR sends
`tanh-higher-zero-v3`, reverting recommendations alone to pre-1.2 rejects that policy.
Recover HR compatibly first, then recommendations, or use a forward fix. Never
rewrite/delete historical results to permit recovery. The additive HR tables `rc_auto_vacancies`/`rc_auto_cursor` may remain when
returning to the immediately prior complete HR API: it has no autonomous profile
dispatcher and ignores this scheduling metadata, so automation pauses without a
new paid POST. Preserve all rows. Profile work and stored results remain owned by
`vp_bindings`/`rc_bindings`, whose existing receiver compatibility guard stays in
force. A rollback therefore stops universal automatic comparison; use a forward
fix when that feature must continue. Stopped-state guards still control activation.

Isolated receiver tests exercise actual JS producer and Python receiver together,
including historical complete layouts. They do not establish installed receiver,
provider quality, or production automatic matching acceptance. Root records reviewed
SHA, artifact hash, private journal and CI URL during the authorized release.
