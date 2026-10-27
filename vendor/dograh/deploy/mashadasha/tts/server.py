"""Internal, bounded OpenAI speech adapter for the optional Edge TTS fallback.

The synthesis provider is Microsoft's online Edge service, not a local model.
Never expose this service publicly or log submitted interview text.
"""

import asyncio
import contextlib
import hmac
import logging
import os
import time
from typing import AsyncIterator, Literal, Optional

import edge_tts
import anyio
import aiohttp
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("dograh.edge_tts")
MAX_CONCURRENCY = int(os.environ.get("TTS_MAX_CONCURRENCY", "1"))
slots = asyncio.Semaphore(MAX_CONCURRENCY)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    model: Literal["edge-tts", "gpt-4o-mini-tts", "tts-1"] = "gpt-4o-mini-tts"
    # Pipecat's OpenAI client rejects non-OpenAI voice IDs before sending HTTP.
    voice: Literal["alloy", "nova", "onyx", "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"] = "alloy"
    response_format: Literal["pcm"] = "pcm"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    instructions: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("input")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("input must contain speech")
        return value


def resolve_voice(voice: str) -> str:
    return {
        "alloy": "ru-RU-SvetlanaNeural",
        "nova": "ru-RU-SvetlanaNeural",
        "onyx": "ru-RU-DmitryNeural",
    }.get(voice, voice)


async def edge_audio(request: SpeechRequest) -> AsyncIterator[bytes]:
    """Retry transport setup only; never replay speech after audio has begun."""
    async def first_audio(stream):
        async for event in stream:
            if event["type"] == "audio" and event["data"]:
                return event["data"]
        raise RuntimeError("TTS provider returned no audio")

    for attempt in range(3):
        started = False
        communicator = edge_tts.Communicate(
            request.input, resolve_voice(request.voice),
            rate=f"{round((request.speed - 1.0) * 100):+d}%",
            connect_timeout=3,
            receive_timeout=5,
            proxy=os.environ.get("TTS_PROXY") or None,
        )
        stream = communicator.stream()
        try:
            # This bound includes DNS, CONNECT, TLS, metadata and first audio.
            first = await asyncio.wait_for(first_audio(stream), timeout=5)
            started = True
            yield first
            async for event in stream:
                if event["type"] == "audio" and event["data"]:
                    yield event["data"]
            return
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            if started or attempt == 2:
                raise
            logger.warning("TTS pre-audio transport retry %d: %s", attempt + 1, type(exc).__name__)
        finally:
            await stream.aclose()
        await asyncio.sleep(0.1 * (attempt + 1))


async def pcm_stream(request: SpeechRequest) -> AsyncIterator[bytes]:
    """Decode incrementally; keep no transcript/audio cache and reap on cancel."""
    process = await asyncio.create_subprocess_exec(
        os.environ.get("FFMPEG_BIN", "ffmpeg"),
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-threads", "1", "-probesize", "32", "-analyzeduration", "0",
        "-f", "mp3", "-i", "pipe:0",
        "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
        "-threads", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None

    async def feed() -> None:
        try:
            async for data in edge_audio(request):
                process.stdin.write(data)
                await process.stdin.drain()
        finally:
            process.stdin.close()

    producer = asyncio.create_task(feed())
    remainder = b""
    deadline = time.monotonic() + 90
    try:
        while True:
            timeout = min(20, deadline - time.monotonic())
            if timeout <= 0:
                raise TimeoutError("TTS synthesis time limit reached")
            chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=timeout)
            if not chunk:
                break
            chunk = remainder + chunk
            end = len(chunk) - len(chunk) % 2
            remainder = chunk[end:]
            if end:
                yield chunk[:end]
        await producer
        returncode = await asyncio.wait_for(process.wait(), timeout=3)
        if returncode != 0 or remainder:
            raise RuntimeError("TTS PCM decode failed")
    finally:
        # Starlette disconnects cancel the surrounding AnyIO scope repeatedly;
        # shield resource cleanup so no subprocess or occupied slot survives.
        with anyio.move_on_after(3, shield=True):
            producer.cancel()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer
            await process.wait()


class AudioResponse(StreamingResponse):
    """Own the acquired slot even if HTTP response startup/disconnect fails."""

    def __init__(self, body, source, **kwargs):
        super().__init__(body, **kwargs)
        self.source = source

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                with anyio.move_on_after(3, shield=True):
                    await self.source.aclose()
            finally:
                slots.release()


def require_auth(authorization: Optional[str]) -> None:
    token = os.environ.get("TTS_API_KEY", "")
    if not token:
        raise HTTPException(503, "TTS adapter credential is not configured")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Invalid TTS adapter credential")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "provider": "edge-tts", "format": "pcm_s16le", "sample_rate": 24000}


@app.get("/v1/models")
async def models(authorization: Optional[str] = Header(default=None)):
    # Dograh checks OpenAI-compatible credentials with client.models.list()
    # before opening WebRTC, including when this provider is used only for TTS.
    require_auth(authorization)
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": 0, "owned_by": "edge-tts-adapter"}
            for name in ("edge-tts", "gpt-4o-mini-tts", "tts-1")
        ],
    }


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    if request.instructions:
        raise HTTPException(400, "Edge TTS does not support acting instructions")
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        raise HTTPException(429, "TTS concurrency limit reached", headers={"Retry-After": "1"})

    stream = pcm_stream(request)
    try:
        first = await asyncio.wait_for(stream.__anext__(), timeout=20)
    except (Exception, asyncio.CancelledError) as exc:
        try:
            with anyio.move_on_after(3, shield=True):
                await stream.aclose()
        finally:
            slots.release()
        if isinstance(exc, asyncio.CancelledError):
            raise
        # Do not return/log provider errors containing URLs or submitted text.
        logger.warning("TTS provider failed before audio: %s", type(exc).__name__)
        raise HTTPException(502, "TTS provider unavailable") from None

    async def body() -> AsyncIterator[bytes]:
        try:
            yield first
            async for chunk in stream:
                yield chunk
        except Exception as exc:
            logger.warning("TTS provider failed during audio: %s", type(exc).__name__)
            raise RuntimeError("TTS stream interrupted") from None

    return AudioResponse(
        body(), stream, media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Audio-Sample-Rate": "24000", "X-Audio-Channels": "1"},
    )
