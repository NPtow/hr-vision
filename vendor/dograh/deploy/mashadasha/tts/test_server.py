import asyncio
import struct
import subprocess

import httpx
import pytest
import pytest_asyncio

import server


@pytest.fixture
def mp3():
    return subprocess.check_output([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "sine=frequency=440:duration=1.5", "-ar", "24000", "-ac", "1",
        "-f", "mp3", "pipe:1",
    ])


@pytest.fixture
def fake_edge(monkeypatch, mp3):
    observed = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            observed.update(text=text, voice=voice, **kwargs)

        async def stream(self):
            for start in range(0, len(mp3), 503):
                yield {"type": "audio", "data": mp3[start:start + 503]}
                await asyncio.sleep(0)

    monkeypatch.setattr(server.edge_tts, "Communicate", FakeCommunicate)
    return observed


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setenv("TTS_API_KEY", "synthetic-test-token")
    server.slots = asyncio.Semaphore(1)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://tts")


@pytest.mark.asyncio
async def test_openai_request_returns_pcm24k(client, fake_edge):
    async with client:
        response = await client.post("/v1/audio/speech", headers={"Authorization": "Bearer synthetic-test-token"}, json={
            "input": "Синтетический тест", "voice": "alloy", "model": "gpt-4o-mini-tts", "response_format": "pcm",
        })
    assert response.status_code == 200
    assert response.headers["x-audio-sample-rate"] == "24000"
    assert response.headers["cache-control"] == "no-store"
    assert not response.content.startswith((b"RIFF", b"ID3"))
    assert len(response.content) % 2 == 0
    assert 1.4 < len(response.content) / 48000 < 1.7
    samples = struct.unpack("<" + "h" * (len(response.content) // 2), response.content)
    assert max(samples) > 100
    assert fake_edge["voice"] == "ru-RU-SvetlanaNeural"
    assert not server.slots.locked()


@pytest.mark.asyncio
async def test_explicit_egress_proxy_is_passed_to_edge(client, fake_edge, monkeypatch):
    monkeypatch.setenv("TTS_PROXY", "http://172.18.0.1:10818")
    async with client:
        response = await client.post("/v1/audio/speech", headers={"Authorization": "Bearer synthetic-test-token"}, json={"input": "Синтетический тест"})
    assert response.status_code == 200
    assert fake_edge["proxy"] == "http://172.18.0.1:10818"


@pytest.mark.asyncio
async def test_rejects_unauthenticated_input(client):
    async with client:
        response = await client.post("/v1/audio/speech", json={"input": "test"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dograh_models_preflight_checks_auth_and_lists_supported_aliases(client):
    async with client:
        unauthorized = await client.get("/v1/models")
        authorized = await client.get("/v1/models", headers={"Authorization": "Bearer synthetic-test-token"})
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    payload = authorized.json()
    assert payload["object"] == "list"
    assert {model["id"] for model in payload["data"]} == {"edge-tts", "gpt-4o-mini-tts", "tts-1"}
    assert all(model["object"] == "model" for model in payload["data"])


@pytest.mark.asyncio
async def test_provider_error_is_502_not_empty_200(client, monkeypatch):
    class BrokenCommunicate:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            raise RuntimeError("synthetic private content must not leak")
            yield

    monkeypatch.setattr(server.edge_tts, "Communicate", BrokenCommunicate)
    async with client:
        response = await client.post("/v1/audio/speech", headers={"Authorization": "Bearer synthetic-test-token"}, json={"input": "test"})
    assert response.status_code == 502
    assert "private" not in response.text
    assert not server.slots.locked()


@pytest.mark.asyncio
async def test_busy_requests_do_not_start_more_ffmpeg(client, monkeypatch):
    await server.slots.acquire()
    async with client:
        response = await client.post("/v1/audio/speech", headers={"Authorization": "Bearer synthetic-test-token"}, json={"input": "test"})
    assert response.status_code == 429
    server.slots.release()


@pytest.mark.asyncio
async def test_closing_stream_reaps_ffmpeg(fake_edge, monkeypatch):
    started = []
    original = asyncio.create_subprocess_exec

    async def observe(*args, **kwargs):
        process = await original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    stream = server.pcm_stream(server.SpeechRequest(input="Синтетический тест"))
    chunk = await stream.__anext__()
    assert chunk
    await stream.aclose()
    assert len(started) == 1
    assert started[0].returncode is not None


@pytest.mark.asyncio
async def test_response_start_failure_releases_slot(client, fake_edge):
    response = await server.speech(server.SpeechRequest(input="test"), "Bearer synthetic-test-token")

    async def send(message):
        raise OSError("synthetic client disconnected before headers")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(Exception):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert not server.slots.locked()


@pytest.mark.asyncio
async def test_anyio_disconnect_releases_slot(client, fake_edge):
    response = await server.speech(server.SpeechRequest(input="test"), "Bearer synthetic-test-token")
    first_body = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            first_body.set()
            await asyncio.sleep(1)

    async def receive():
        await first_body.wait()
        return {"type": "http.disconnect"}

    await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
    assert not server.slots.locked()


@pytest.mark.asyncio
async def test_transport_failure_before_audio_retries(monkeypatch):
    attempts = []

    class FlakyCommunicate:
        def __init__(self, *args, **kwargs):
            attempts.append(kwargs)

        async def stream(self):
            if len(attempts) == 1:
                raise asyncio.TimeoutError()
            yield {"type": "SentenceBoundary", "text": "ignored"}
            yield {"type": "audio", "data": b"synthetic-mp3"}

    monkeypatch.setattr(server.edge_tts, "Communicate", FlakyCommunicate)
    audio = [chunk async for chunk in server.edge_audio(server.SpeechRequest(input="test"))]
    assert audio == [b"synthetic-mp3"]
    assert len(attempts) == 2
    assert attempts[0]["connect_timeout"] == 3


@pytest.mark.asyncio
async def test_midstream_transport_error_never_replays_audio(monkeypatch):
    attempts = []

    class BrokenStream:
        def __init__(self, *args, **kwargs):
            attempts.append(kwargs)

        async def stream(self):
            yield {"type": "audio", "data": b"first-audio"}
            raise asyncio.TimeoutError()

    monkeypatch.setattr(server.edge_tts, "Communicate", BrokenStream)
    audio = []
    with pytest.raises(asyncio.TimeoutError):
        async for chunk in server.edge_audio(server.SpeechRequest(input="test")):
            audio.append(chunk)
    assert audio == [b"first-audio"]
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_pre_audio_retries_stop_at_three(monkeypatch):
    attempts = []

    class Unavailable:
        def __init__(self, *args, **kwargs):
            attempts.append(kwargs)

        async def stream(self):
            raise asyncio.TimeoutError()
            yield

    monkeypatch.setattr(server.edge_tts, "Communicate", Unavailable)
    with pytest.raises(asyncio.TimeoutError):
        async for _ in server.edge_audio(server.SpeechRequest(input="test")):
            pass
    assert len(attempts) == 3
