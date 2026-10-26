# Shared JEV transport

Standard-library package extracted from the candidate service's
`assessment_core.providers.jev`. Public imports remain
`JevClient`, `JevConfig`, `JevError`, and `validate_response`. It performs one
bounded POST with no automatic retries and retains strict typed-response,
probability, safe-error and private raw-rejection behavior.

`is_choice_probability_distribution(values) -> bool` is the common validation
policy used by the transport and recommendation consumer. It never changes its
input. Finite numeric values must lie in `[0,1]`, with positive total mass.
The historical `abs(sum(p)-1) <= 1e-5` path still accepts higher precision output.
Otherwise every value must lie on the `0.01` grid (scaled floating-point
tolerance `1e-9`), and `abs(sum(p)-1) <= N*0.005 + 1e-6`. This is accumulated
serialization error, not an inferred or normalized posterior. Exact option
keys and selected argmax remain mandatory. The selected value may differ from
the maximum by at most four binary floating-point ULPs of that maximum, allowing
machine-level ties such as `0.33999999999999997` versus `0.34`. This does not use
the decimal rounding tolerance and never rewrites the provider's choice or
probabilities. This exception applies only
to Choice; Score mass and weighted-mean checks retain their original rules.

The bounded rounding policy follows the merged Vercel AI SDK implementation,
pinned to commit `20c4031d1fc59aab54b67c16679e3981374690ab`:

- [Native TypeSafe adapter](https://github.com/vercel/ai/blob/20c4031d1fc59aab54b67c16679e3981374690ab/packages/typesafe-ai/src/typesafe-ai-decision-model.ts#L324-L325)
  declares two decimal places while returning provider probabilities unchanged.
- [Distribution validator](https://github.com/vercel/ai/blob/20c4031d1fc59aab54b67c16679e3981374690ab/packages/ai/src/decide/validate-decision.ts#L202-L230)
  uses `1e-6 + N * (0.5 * 10**(-decimals))` for the sum bound. The grid gate,
  positive-mass requirement and argmax check are our stricter guards.
- [Native TypeSafe OpenAPI](https://api.typesafe.ai/openapi.json), checked
  2026-10-26, says Choice probabilities sum to approximately 1, without an
  explicit precision bound. Thus this is an explicit client interoperability
  policy grounded in the pinned adapter, rather than a stronger native API
  guarantee. No score, confidence, choice, or stored raw probability is repaired.

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
