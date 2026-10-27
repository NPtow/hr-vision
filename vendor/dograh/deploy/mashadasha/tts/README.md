# Russian TTS adapter installed on YC

The Dograh MashaDasha compose stack runs this private service as
`dograh-mashadasha-tts:edge7.2.8`. Its provider URL is `http://tts:8000/v1`;
`enable_tts.py` prepares the owner configuration and `bootstrap.py` persists it.
It uses `edge-tts==7.2.8` to access
Microsoft Edge's online synthesis, then incrementally decodes MP3 to PCM with
ffmpeg. It is not offline synthesis or a contracted Azure Speech API, has no
SLA, and can stop working if Microsoft's browser endpoint changes. The adapter
does not store or log text/audio; the requested speech text is sent to Microsoft.

The `edge-tts` dependency is LGPLv3 (one unused subtitle module is MIT); its
license does not establish terms for Microsoft's service. Verify commercial
service suitability separately before using it for real candidates.

## Wire format

`POST /v1/audio/speech`, `Authorization: Bearer <TTS_API_KEY>`, JSON:

```json
{"model":"gpt-4o-mini-tts","voice":"alloy","input":"Здравствуйте!","response_format":"pcm"}
```

Returns an HTTP byte stream: raw **signed 16-bit little-endian, mono, 24,000 Hz**,
without a WAV header. This is the format Pipecat's `OpenAITTSService` consumes.
`alloy` and `nova` select `ru-RU-SvetlanaNeural`; `onyx` selects Dmitry. OpenAI
model/voice IDs are compatibility aliases, not claims about the synthesizer.
Acting `instructions` are rejected since Edge cannot honor them. Limit: one
active request, 4,000 text characters, bounded connection/read timeouts.

Authenticated `GET /v1/models` returns the three supported compatibility aliases.
Dograh uses this endpoint to validate TTS credentials before starting WebRTC;
it confirms local adapter authentication, not Microsoft's current availability.

Dograh configuration: provider `openai`, model `gpt-4o-mini-tts`, voice `alloy`,
base URL `http://tts:8000/v1`, API key equal to the private adapter token. Ensure
Dograh's runtime service URL policy permits this internal endpoint. The current
Dograh OpenAI factory uses Pipecat's default `alloy` even if another voice is set.

## Container integration

Build this directory as context. The Dockerfile reuses existing
`dograhai/dograh-api:1.48.0` layers including ffmpeg but runs only uvicorn.
The vendored Edge 7.2.8 and tabulate 0.9.0 wheels are verified against `SHA256SUMS`
from PyPI and installed into `/opt/edge-tts` with no network/dependency installation.
aiohttp, certifi and typing_extensions are reused from Dograh (imports checked at
build). The actual 1.48.0 image lacks tabulate, so that pure-Python dependency is
included explicitly.
On the existing Dograh Docker network, use no host port
mapping and a random private `TTS_API_KEY`. The installed limits are `mem_limit: 128m`, `cpus: 0.50`,
`pids_limit: 50`, `init: true`, `read_only: true`, `cap_drop: [ALL]`, and
`security_opt: [no-new-privileges:true]`. Root deployment uses secret variable
`TTS_AUTH_TOKEN`; pass it to the container as `TTS_API_KEY`. Do not expose this through Caddy.
Readiness `/health` confirms the adapter process, not Microsoft availability.

Direct Edge websocket connections from YC can time out. Set `TTS_PROXY` to the
private HTTP CONNECT egress proxy and allow exactly `speech.platform.bing.com:443`
there, alongside any existing destinations. `Communicate(proxy=...)` is explicit;
aiohttp's default does not automatically apply ordinary HTTPS_PROXY variables.

Intermittent connection timeouts are retried at most three times, only before
the first upstream audio bytes. Each setup attempt has a 3-second connection
timeout and a 5-second total first-audio deadline; all attempts remain inside the
endpoint's 20-second preflight budget. After audio starts, transport failures are
propagated without replaying speech. Exhausted attempts return HTTP 502.

## Validation

```sh
python -m pytest -q test_server.py
```

Tests cover the OpenAI-compatible request and PCM format using a synthetic
tone, authentication, provider failure, the concurrency cap and ffmpeg cleanup
when a stream is cancelled. No real candidate material is used. Live proof and
measurements are recorded separately in `validation.json`. The checked local
sample is `sample.wav` (ignored by Git): 9.48s Russian speech, 455,040 PCM bytes,
121 streamed chunks, 67.55MiB peak observed process-tree RSS, clean midstream
abort and successful subsequent request. First chunk took 5.944s on the Mac;
decoder-only first chunk took 43ms. Those measurements describe the Mac only.

On 2026-10-06 the YC deployment returned HTTP 200 with 335,232 PCM bytes for
a direct synthetic request. First chunk: 1.079s; total: 1.994s. This confirms
the adapter and upstream synthesis on YC for that request. It does not prove
browser WebRTC, STT, interview routing, or sustained concurrency. Full voice
session evidence belongs in `/var/lib/dograh/verification/` on outreach.
`validation.json` remains the original Mac measurement; it is not a YC report.

Run `python validate_live.py` explicitly for another online synthetic sample.

The YC container subsequently reproduced a direct Edge websocket connection
timeout. After enabling the private allowlisted egress route, three real Russian
requests passed with first PCM at 1.671s, 1.748s and 0.519s. The longest returned
455,040 non-silent PCM bytes (9.48s speech). Both containers were healthy with zero
restarts/OOMs; idle TTS memory was 49.76MiB. See `validation-yc.json` for the exact
synthetic texts, measurements and verification boundary. The egress healthcheck
uses direct `CMD` execution so shell quoting cannot corrupt its literal IP.

Primary sources checked 2026-10-06:

- https://github.com/rany2/edge-tts
- https://pypi.org/project/edge-tts/7.2.8/
- https://github.com/rany2/edge-tts/blob/master/LICENSE
- Local `pipecat/src/pipecat/services/openai/tts.py`
- Local `api/services/configuration/registry.py`

After the bounded pre-audio retry fix, all 12 regression tests and three more YC
requests passed, with first PCM at 1.009s, 0.625s and 3.112s. See
`validation-retries-yc.json`. A subsequent short browser exchange passed without
provider errors; `../browser-voice-verification.json` records run 16 and its
synthetic-audio verification boundary.
