"""Proxy-aware transport for Deepgram SDK 7.11's listen.v1 async client.

The SDK selects websockets.legacy, which ignores WSS_PROXY. Use the installed
modern transport only for this Deepgram endpoint. Audio is never replayed.
"""

from contextlib import asynccontextmanager

from deepgram.core.api_error import ApiError
from websockets.asyncio.client import connect as modern_connect
from websockets.exceptions import InvalidStatus


@asynccontextmanager
async def connect(uri, *, extra_headers=None, **kwargs):
    # The modern client discovers WSS_PROXY and honors NO_PROXY. Its default
    # certificate/hostname verification stays enabled; no custom TLS context.
    kwargs.setdefault("proxy", True)
    kwargs.setdefault("open_timeout", 3.0)
    kwargs.setdefault("close_timeout", 1.0)
    connection = None
    for attempt in range(3):
        try:
            connection = await modern_connect(
                uri, additional_headers=extra_headers, **kwargs
            )
            break
        except InvalidStatus as error:
            # The generated SDK otherwise copies request headers (including
            # Authorization) into ApiError. Preserve status, never credentials.
            raise ApiError(
                status_code=error.response.status_code,
                headers={},
                body="Deepgram rejected the WebSocket handshake.",
            ) from None
        except (OSError, TimeoutError):
            if attempt == 2:
                raise

    try:
        yield connection
    except BaseException:
        try:
            await connection.close()
        except Exception:
            # Cleanup failure must not hide cancellation or the original
            # stream/audio error. The caller still owns that failure.
            pass
        raise
    else:
        # Only establishing the handshake is retried. Once yielded, normal
        # Pipecat connection lifecycle owns reconnects and any audio buffering.
        await connection.close()
