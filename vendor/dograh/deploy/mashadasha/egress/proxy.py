#!/usr/bin/env python3
"""Private speech-provider HTTP CONNECT adapter to the host's existing SOCKS5.

No TLS interception: the calling client verifies the provider's certificate and
sends its API key inside the encrypted tunnel. Never log request headers,
credentials, audio, transcripts, or exception messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import os


ALLOWED_HOSTS = frozenset({"api.deepgram.com", "api.eu.deepgram.com", "speech.platform.bing.com"})


@dataclass(frozen=True)
class Settings:
    bind_host: str = "172.18.0.1"
    bind_port: int = 10818
    client_subnet: str = "172.18.0.0/16"
    socks_host: str = "127.0.0.1"
    socks_port: int = 10808
    max_connections: int = 16
    handshake_timeout: float = 8.0
    idle_timeout: float = 180.0


def settings_from_env() -> Settings:
    settings = Settings(
        bind_host=os.getenv("DEEPGRAM_EGRESS_BIND", "172.18.0.1"),
        bind_port=int(os.getenv("DEEPGRAM_EGRESS_PORT", "10818")),
        client_subnet=os.getenv("DEEPGRAM_EGRESS_CLIENT_SUBNET", "172.18.0.0/16"),
    )
    address = ipaddress.ip_address(settings.bind_host)
    subnet = ipaddress.ip_network(settings.client_subnet)
    if address.is_unspecified or not address.is_private or address not in subnet:
        raise ValueError("Bind must be a specific address in the private client subnet")
    if subnet.prefixlen < 16:
        raise ValueError("Client subnet is too broad")
    if not 1024 <= settings.bind_port <= 65535:
        raise ValueError("Bind port must be unprivileged")
    return settings


def parse_connect(headers: bytes) -> tuple[str, int]:
    """Return a validated allowlisted target; never include input in errors."""
    try:
        request_line = headers.split(b"\r\n", 1)[0].decode("ascii")
        method, authority, version = request_line.split(" ")
    except (ValueError, UnicodeDecodeError):
        raise ValueError("bad_request") from None
    if method != "CONNECT":
        raise ValueError("method_not_allowed")
    if version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ValueError("bad_request")
    try:
        host, port_text = authority.rsplit(":", 1)
        port = int(port_text)
    except ValueError:
        raise ValueError("bad_request") from None
    if host.lower() not in ALLOWED_HOSTS or port != 443:
        raise ValueError("destination_not_allowed")
    return host.lower(), port


async def close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
    except Exception:
        pass


class DeepgramConnectProxy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client_subnet = ipaddress.ip_network(settings.client_subnet)
        self.active_connections = 0

    async def open_upstream(self, host: str, port: int):
        reader, writer = await asyncio.open_connection(
            self.settings.socks_host, self.settings.socks_port
        )
        try:
            writer.write(b"\x05\x01\x00")  # SOCKS5, no-auth method only.
            await writer.drain()
            if await reader.readexactly(2) != b"\x05\x00":
                raise ConnectionError("socks_auth_failed")
            domain = host.encode("ascii")
            writer.write(
                b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain
                + port.to_bytes(2, "big")
            )
            await writer.drain()
            response = await reader.readexactly(4)
            if response[0] != 5 or response[1] != 0 or response[2] != 0:
                raise ConnectionError("socks_connect_failed")
            if response[3] == 1:
                address_length = 4
            elif response[3] == 4:
                address_length = 16
            elif response[3] == 3:
                address_length = (await reader.readexactly(1))[0]
            else:
                raise ConnectionError("socks_response_invalid")
            await reader.readexactly(address_length + 2)
            return reader, writer
        except BaseException:
            await close_writer(writer)
            raise

    async def relay(self, reader, writer) -> None:
        while True:
            chunk = await asyncio.wait_for(
                reader.read(65536), timeout=self.settings.idle_timeout
            )
            if not chunk:
                return
            writer.write(chunk)
            await writer.drain()

    @staticmethod
    async def respond(writer, status: int) -> None:
        reason = {
            200: "Connection Established", 400: "Bad Request", 403: "Forbidden",
            405: "Method Not Allowed", 502: "Bad Gateway", 503: "Service Unavailable",
        }[status]
        writer.write(f"HTTP/1.1 {status} {reason}\r\n\r\n".encode("ascii"))
        await writer.drain()

    async def handle(self, reader, writer) -> None:
        upstream_writer = None
        tasks = []
        counted = False
        connected = False
        try:
            peer = writer.get_extra_info("peername")
            if not peer or ipaddress.ip_address(peer[0]) not in self.client_subnet:
                await self.respond(writer, 403)
                return
            if self.active_connections >= self.settings.max_connections:
                await self.respond(writer, 503)
                return
            self.active_connections += 1
            counted = True
            try:
                headers = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), timeout=self.settings.handshake_timeout
                )
                if len(headers) > 8192:
                    raise ValueError("bad_request")
                host, port = parse_connect(headers)
            except (ValueError, asyncio.LimitOverrunError) as error:
                status = {"method_not_allowed": 405, "destination_not_allowed": 403}.get(str(error), 400)
                await self.respond(writer, status)
                return
            upstream_reader, upstream_writer = await asyncio.wait_for(
                self.open_upstream(host, port), timeout=self.settings.handshake_timeout
            )
            await self.respond(writer, 200)
            connected = True
            tasks = [
                asyncio.create_task(self.relay(reader, upstream_writer)),
                asyncio.create_task(self.relay(upstream_reader, writer)),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (ConnectionError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            if not connected:
                try:
                    await self.respond(writer, 502)
                except Exception:
                    pass
        except Exception as error:
            # Class names are safe; exception text can contain caller input.
            print(json.dumps({"event": "egress_error", "type": type(error).__name__}), flush=True)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await close_writer(upstream_writer)
            await close_writer(writer)
            if counted:
                self.active_connections -= 1

    async def start(self):
        return await asyncio.start_server(
            self.handle, self.settings.bind_host, self.settings.bind_port, limit=8192
        )


async def main() -> None:
    settings = settings_from_env()
    server = await DeepgramConnectProxy(settings).start()
    print(json.dumps({
        "event": "egress_ready", "bind": settings.bind_host,
        "port": settings.bind_port, "allowed_hosts": sorted(ALLOWED_HOSTS),
    }), flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
