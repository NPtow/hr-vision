from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("deepgram_egress", Path(__file__).with_name("proxy.py"))
proxy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proxy
spec.loader.exec_module(proxy)


class AllowlistTests(unittest.TestCase):
    def test_only_explicit_speech_provider_tls_destinations(self):
        self.assertEqual(proxy.ALLOWED_HOSTS, {"api.deepgram.com", "api.eu.deepgram.com", "speech.platform.bing.com"})
        for host in proxy.ALLOWED_HOSTS:
            self.assertEqual(proxy.parse_connect(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode()), (host, 443))
        for target in ("example.com:443", "api.deepgram.com:80", "127.0.0.1:443", "api.deepgram.com.evil.test:443", "user@api.deepgram.com:443", "api.deepgram.com.:443", "speech.platform.bing.com:80", "speech.platform.bing.com.evil.test:443"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "destination_not_allowed"):
                proxy.parse_connect(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())

    def test_http_forwarding_and_malformed_requests_are_rejected(self):
        for request in (b"GET https://api.deepgram.com HTTP/1.1\r\n\r\n", b"CONNECT\r\n\r\n", b"CONNECT \xff:443 HTTP/1.1\r\n\r\n"):
            with self.assertRaises(ValueError):
                proxy.parse_connect(request)

    def test_wildcard_bind_refused(self):
        with patch.dict("os.environ", {"DEEPGRAM_EGRESS_BIND": "0.0.0.0"}):
            with self.assertRaises(ValueError):
                proxy.settings_from_env()


class TunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socks_targets = []

        async def socks(reader, writer):
            try:
                self.assertEqual(await reader.readexactly(3), b"\x05\x01\x00")
                writer.write(b"\x05\x00"); await writer.drain()
                self.assertEqual(await reader.readexactly(4), b"\x05\x01\x00\x03")
                length = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(length)).decode()
                port = int.from_bytes(await reader.readexactly(2), "big")
                self.socks_targets.append((host, port))
                writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00"); await writer.drain()
                while chunk := await reader.read(1024):
                    writer.write(chunk); await writer.drain()
            finally:
                await proxy.close_writer(writer)

        self.socks = await asyncio.start_server(socks, "127.0.0.1", 0)
        self.settings = proxy.Settings(bind_host="127.0.0.1", bind_port=0, client_subnet="127.0.0.0/8", socks_port=self.socks.sockets[0].getsockname()[1])
        self.adapter = proxy.DeepgramConnectProxy(self.settings)
        self.server = await self.adapter.start()
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close(); self.socks.close()
        await self.server.wait_closed(); await self.socks.wait_closed()
        await asyncio.sleep(0)

    async def request(self, raw):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(raw); await writer.drain()
        return reader, writer, await reader.readuntil(b"\r\n\r\n")

    async def test_connect_relays_binary_data_without_inspection(self):
        reader, writer, response = await self.request(b"CONNECT api.deepgram.com:443 HTTP/1.1\r\nHost: api.deepgram.com\r\n\r\n")
        self.assertIn(b"200 Connection Established", response)
        payload = b"\x00\xffsynthetic-encrypted-payload\x00"
        writer.write(payload); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 1), payload)
        self.assertEqual(self.socks_targets, [("api.deepgram.com", 443)])
        await proxy.close_writer(writer)

    async def test_rejected_destination_never_reaches_socks(self):
        reader, writer, response = await self.request(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b"403 Forbidden", response)
        self.assertEqual(self.socks_targets, [])
        await proxy.close_writer(writer)

    async def test_connection_cap_is_enforced(self):
        self.adapter.settings = replace(self.settings, max_connections=0)
        reader, writer, response = await self.request(b"CONNECT api.deepgram.com:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b"503 Service Unavailable", response)
        self.assertEqual(self.socks_targets, [])
        await proxy.close_writer(writer)

    async def test_non_dograh_source_is_refused(self):
        import ipaddress
        self.adapter.client_subnet = ipaddress.ip_network("172.18.0.0/16")
        reader, writer, response = await self.request(b"CONNECT api.deepgram.com:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b"403 Forbidden", response)
        self.assertEqual(self.socks_targets, [])
        await proxy.close_writer(writer)


if __name__ == "__main__":
    unittest.main()
