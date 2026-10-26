# Shared JEV transport

Standard-library package extracted byte-for-byte from the candidate service's
`assessment_core.providers.jev`. Public imports remain
`JevClient`, `JevConfig`, `JevError`, and `validate_response`. It performs one
bounded POST with no automatic retries and retains strict typed-response,
probability, safe-error and private raw-rejection behavior.

In the checkout, new consumers set
`PYTHONPATH=services/vacancy-profile:packages/jev-client` and import `jev_client`.
Candidate commands keep `PYTHONPATH=services/assessment-core`; the old import
resolves to the same shared module, preserving existing monkeypatch targets.
This is a library copied into each release, not another HTTP service.

Build each service's usual release directory, then freeze the dependencies:

```sh
python3 packages/jev-client/export_release.py /absolute/path/to/release
```

For a candidate release, preserve the existing rubric filename:

```sh
python3 packages/jev-client/export_release.py /absolute/path/to/candidate-release --rubric-name analytics-baseline.json
```

The exported root contains `jev_client/__init__.py`, the named rubric, and
`shared-components.json` with SHA-256 values. The exporter requires exact byte
equality of the shared catalog and candidate catalog, rejects conflicting files
before copying, and emits identical bytes for the same source. Run the service
with that release root on `PYTHONPATH`, as for its own application package.
No monorepo checkout or other service process is needed at runtime.

The candidate compatibility file also supports the installer's direct import
by filesystem path when the exported root is absent from `sys.path`.
Releases containing the compatibility file **must** include the frozen package;
copying only `services/assessment-core` is no longer sufficient. Existing old
releases are unaffected and remain valid rollback targets. The release's rubric
activation guard must hash `jev_client/**/*.py` along with candidate runtime.

Offline validation (no paid provider calls):

```sh
PYTHONPATH=services/assessment-core python3 -m unittest discover -s services/assessment-core/tests
python3 -m unittest discover -s packages/jev-client/tests
```
