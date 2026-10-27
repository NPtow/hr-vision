HTTP materials integration acceptance
=====================================

Run from the repository root:

```sh
/tmp/md-services-venv/bin/python -m pytest services/tests/test_materials_http_integration.py -q
```

The test interpreter needs the dependencies of all three services and pytest.
The suite launches **separate OS processes** for the real `hr_materials_core`,
`source_daily` and `md_transcription` FastAPI apps on loopback TCP. The source
process runs the real Pipeline and Store; its real CoreClient and
TranscriptionClient use HTTP requests. Enqueue, job reads, owner reads, access
resolution, normalization and receipt publication cross actual HTTP boundaries.

Covered:

- A synthetic completed interview binds through the trusted HR owner/room rows.
- Remote video needs no video download during provider transcript acquisition.
- Original VTT bytes are preserved in the object-store fixture. The real
  normalization endpoint handles timestamps, voice labels, entity decoding and
  missing-speaker warnings. Core stores raw reference, normalized segments and
  provenance, without inferring independently verified coverage.
- Upgrading the same binding from remote to owned increments generation, stores
  the video with content-addressed identity and core HEAD verification, and
  preserves asset IDs and the identical transcript version.
- A forwarding HTTP relay returns 503 **after the real core accepted a receipt**.
  The source restarts with its private database, then replays the same durable
  receipt and receives the same IDs. There remains one core receipt, two assets
  and one transcript version.
- An unconfigured real ASR endpoint returns its documented configuration error:
  video remains available, acquisition remains complete, no inference job/input
  is created, and transcription awaits configuration.
- A malformed VTT is rejected by the real normalizer; no empty transcript is
  published, video remains available and acquired raw bytes remain in storage.
- Source credentials cannot read HR dossier/owner materials. A source-process
  SQLite audit guard permits only its private jobs database, exercises a denied
  open, and records all successful SQLite opens. The source has no HR repository
  module import path; its private tables contain no candidate profile, provider
  capability URL or service secrets.

Verification limits:

Provider discovery/resolve/download and object storage are injected local
fixtures. This suite does not verify Daily availability, live media fidelity,
provider SSRF/TLS download behavior, real S3 consistency/signing, inference quality
or a live ASR backend. Inference is explicitly unconfigured. No broker/Celery
delivery or production deployment is exercised: a test process worker invokes
the same Pipeline through its private durable queue. The source SQLite guard
proves these test executions do not open the HR database; it is test enforcement,
not a claim about production OS/container sandboxing. Synthetic records and
temporary volumes are the only data used.
