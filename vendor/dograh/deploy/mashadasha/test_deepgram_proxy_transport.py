"""Narrow regression tests for SDK transport, without app/network dependencies."""

import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest


class ApiError(Exception):
    def __init__(self, *, status_code, headers, body):
        self.status_code, self.headers = status_code, headers
        super().__init__(body)


class InvalidStatus(Exception):
    def __init__(self, status):
        self.response = SimpleNamespace(status_code=status)


class Connection:
    def __init__(self):
        self.closed = False
        self.close_error = None

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class ProxyTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(__file__).parent / "patches/deepgram_proxy_transport.py"
        tree = ast.parse(path.read_text())
        tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)]
        self.calls = []
        self.connection = Connection()
        self.outcomes = [self.connection]
        self.key = "synthetic-secret-not-in-errors"

        async def modern_connect(uri, **kwargs):
            self.calls.append((uri, kwargs))
            result = self.outcomes.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result

        namespace = {
            "asynccontextmanager": asynccontextmanager,
            "modern_connect": modern_connect,
            "ApiError": ApiError,
            "InvalidStatus": InvalidStatus,
        }
        exec(compile(tree, str(path), "exec"), namespace)
        self.connect = namespace["connect"]

    async def open(self):
        async with self.connect(
            "wss://api.deepgram.com/v1/listen",
            extra_headers={"Authorization": "Token " + self.key},
        ) as connection:
            return connection

    async def test_proxy_and_tls_defaults_and_header_mapping(self):
        self.assertIs(await self.open(), self.connection)
        self.assertTrue(self.connection.closed)
        _, kwargs = self.calls[0]
        self.assertEqual(kwargs["additional_headers"], {"Authorization": "Token " + self.key})
        self.assertNotIn("extra_headers", kwargs)
        self.assertNotIn("ssl", kwargs)
        self.assertTrue(kwargs["proxy"])
        self.assertEqual(kwargs["open_timeout"], 3.0)
        self.assertEqual(kwargs["close_timeout"], 1.0)

    async def test_transport_retries_only_before_yield(self):
        self.outcomes = [TimeoutError(), OSError(), self.connection]
        self.assertIs(await self.open(), self.connection)
        self.assertEqual(len(self.calls), 3)

    async def test_transport_failure_has_finite_limit(self):
        self.outcomes = [TimeoutError(), TimeoutError(), TimeoutError(), self.connection]
        with self.assertRaises(TimeoutError):
            await self.open()
        self.assertEqual(len(self.calls), 3)

    async def test_http_errors_preserve_status_and_never_copy_credentials(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.calls.clear()
                self.outcomes = [InvalidStatus(status), self.connection]
                with self.assertRaises(ApiError) as caught:
                    await self.open()
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.headers, {})
                self.assertNotIn(self.key, str(caught.exception))
                self.assertEqual(len(self.calls), 1)

    async def test_audio_phase_exception_is_never_retried(self):
        with self.assertRaises(OSError):
            async with self.connect("wss://api.deepgram.com/v1/listen"):
                raise OSError("synthetic send failure")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.connection.closed)

    async def test_cleanup_cannot_hide_original_stream_failure(self):
        self.connection.close_error = RuntimeError("cleanup failed")
        with self.assertRaisesRegex(OSError, "original stream failure"):
            async with self.connect("wss://api.deepgram.com/v1/listen"):
                raise OSError("original stream failure")
        self.assertEqual(len(self.calls), 1)

    async def test_cleanup_cannot_hide_cancellation(self):
        self.connection.close_error = RuntimeError("cleanup failed")
        with self.assertRaises(asyncio.CancelledError):
            async with self.connect("wss://api.deepgram.com/v1/listen"):
                raise asyncio.CancelledError()
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
