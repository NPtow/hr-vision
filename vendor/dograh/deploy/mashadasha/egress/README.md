# Speech-provider egress for the Dograh pilot

The outreach VM intermittently times out while establishing TLS sessions
to Deepgram. This was reproduced as UID 999 both before and after importing the
API application. HTTP/SOCKS proxy environment variables were absent in both the
running uvicorn process and diagnostic process. API keys and both US/EU endpoints
also succeeded, so the observed failure was not a rejected credential. The
existing host xray SOCKS5 route passed the initial three-request comparison, but
later tests also reproduced intermittent TLS timeouts through that route. The
HTTP CONNECT exchange itself succeeded in 17 ms. Changing TLS version, ALPN or
key-share group did not isolate a cause. This adapter scopes egress; it does not
by itself establish reliable provider reachability.

`proxy.py` is a standard-library HTTP CONNECT adapter to that existing xray
listener at `127.0.0.1:10808`. It runs in a separate host-network container and
binds only `172.18.0.1:10818`, the Dograh Docker bridge gateway. Clients must be
inside `172.18.0.0/16`. Its fixed destination allowlist contains only
`api.deepgram.com:443`, `api.eu.deepgram.com:443` and
`speech.platform.bing.com:443`. The last destination was added after direct Edge
TTS websocket connection timeouts were reproduced inside its YC container.

The adapter does not decrypt TLS. HTTPX and WebSockets clients retain certificate
verification and send the provider API key inside the encrypted connection.
It logs neither request headers nor tunnel content. No host routing rule, xray
setting, existing service, or API key changes are required.

The compose service uses the already available official Dograh API image with
an overridden Python entrypoint and a read-only source bind mount. It runs as
UID/GID 999, with a read-only filesystem, all capabilities dropped,
`no-new-privileges`, 64 MiB memory, 0.2 CPU and 32 PIDs. The server itself caps
connections at 16, headers at 8 KiB, connection setup at 8 seconds, and idle
tunnels at 180 seconds.

Only the Dograh API receives:

```text
HTTPS_PROXY=http://172.18.0.1:10818
WSS_PROXY=http://172.18.0.1:10818
NO_PROXY=localhost,127.0.0.1,::1,postgres,redis,minio,tts,.sslip.io
```

The existing CPA endpoint and internal services bypass the adapter. Additional
HTTPS services would require an explicit bypass or a separately reviewed route;
the adapter is intentionally not a general-purpose open proxy. The deployment
must retain the documented Docker subnet or update its private bind and source
subnet together. Do not change the bind to `0.0.0.0`.

## Native speech transport

Deepgram SDK 7.11.0 explicitly imports `websockets.legacy.client.Connect` for
its async `listen.v1` client. That implementation ignores proxy environment
variables. A native service-factory probe confirmed it opened direct sockets
to Deepgram even with `WSS_PROXY` set.

`patch_deepgram_proxy.py` applies a SHA-guarded, single-import replacement to
that SDK module during the API image build and installs
`patches/deepgram_proxy_transport.py` beside it. Only async Deepgram listen.v1
uses the adapter. It preserves TLS verification and headers, honors WSS_PROXY,
retries handshake transport failures at most three times with a 3-second open
timeout, and never retries or replays the audio phase. HTTP rejection preserves
the status without copying request Authorization into exception headers.

The native factory/SDK acceptance probe received Metadata for all three US and
three EU attempts; observed sockets connected to `172.18.0.1:10818`. The original
SDK bypassed the proxy and timed out on one of two probes in each region. The
invalid-key test returned HTTP401 with empty exception headers. Regional changes
alone therefore do not repair the routing issue.

The same production factory and native SDK then transcribed the entire 25-second
Russian synthetic CV and returned Metadata in 20.62 seconds through the adapter.
This verifies recognition at the SDK boundary. A subsequent short browser
exchange also passed; see `../browser-voice-verification.json` (run 16).

The TTS container receives only `TTS_PROXY=http://172.18.0.1:10818`. Its adapter
passes that setting explicitly into `edge_tts.Communicate`; no global proxy
environment or TLS verification bypass is needed for TTS.

Local regression test:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 deploy/mashadasha/egress/test_proxy.py
```

An auth response only establishes provider reachability and credential validity.
The deployed pilot passed a short browser exchange with prerecorded synthetic
speech through Web Audio, WebRTC, recognition and speech playback. A physical
microphone and a full-length voice interview were not tested.

The deployment validator uses the official REST token endpoint with a 3-second
timeout. A REST transport failure invokes an authenticated, zero-audio streaming
check (`CloseStream` then `Metadata`), with up to three bounded transport attempts.
Authorization errors fail immediately. Three real-key validator checks passed
in the acceptance test, and a synthetic invalid key was rejected. The installed
Pipecat Deepgram service already reconnects with exponential backoff and a finite
consecutive-failure limit. These measures tolerate some network failures without
accepting an unverified credential.
