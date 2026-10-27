"""Opt-in synthetic online smoke test; does not contact real candidates."""

import asyncio
import datetime
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import wave

import httpx

ROOT = Path(__file__).resolve().parent
TEXT = "Здравствуйте! Я Маша. Расскажите, пожалуйста, о вашем опыте работы и самом интересном проекте."


def processes(parent):
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    table = [tuple(map(int, row.split())) for row in rows.splitlines() if row.strip()]
    ids = {parent}
    while True:
        descendants = {pid for pid, ppid, rss in table if ppid in ids}
        if descendants <= ids:
            break
        ids |= descendants
    return [(pid, rss) for pid, _, rss in table if pid in ids]


async def main():
    token = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (ROOT / "validation-server.log").open("w")
    proc = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
        "--port", str(port), "--no-access-log",
    ], cwd=ROOT, env={**os.environ, "TTS_API_KEY": token}, stdout=log, stderr=log)
    peak_rss = 0
    stopped = False

    async def monitor():
        nonlocal peak_rss
        while not stopped:
            peak_rss = max(peak_rss, sum(rss for _, rss in processes(proc.pid)))
            await asyncio.sleep(0.1)

    task = asyncio.create_task(monitor())
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            for _ in range(100):
                try:
                    if (await client.get("/health")).is_success:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.1)
            headers = {"Authorization": f"Bearer {token}"}
            body = {"model": "gpt-4o-mini-tts", "voice": "alloy", "response_format": "pcm", "input": TEXT}
            started = time.monotonic()
            pcm = bytearray()
            first_ms = None
            chunks = 0
            async with client.stream("POST", "/v1/audio/speech", headers=headers, json=body) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if first_ms is None:
                        first_ms = round((time.monotonic() - started) * 1000)
                    chunks += 1
                    pcm.extend(chunk)
            elapsed = round((time.monotonic() - started) * 1000)
            assert len(pcm) > 48000 and len(pcm) % 2 == 0
            with wave.open(str(ROOT / "sample.wav"), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(pcm)

            # Disconnect mid-response, then verify resource cleanup and re-entry.
            async with client.stream("POST", "/v1/audio/speech", headers=headers, json={**body, "input": TEXT * 8}) as response:
                response.raise_for_status()
                async for _ in response.aiter_bytes():
                    break
            await asyncio.sleep(0.3)
            children_after_abort = [pid for pid, _ in processes(proc.pid) if pid != proc.pid]
            assert not children_after_abort, children_after_abort
            response = await client.post("/v1/audio/speech", headers=headers, json={**body, "input": "Проверка завершена."})
            assert response.status_code == 200 and len(response.content) > 1000
            result = {
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "environment": "macOS local Python3.9.6; NOT YC container verification",
                "edge_tts_version": "7.2.8",
                "voice": "ru-RU-SvetlanaNeural",
                "synthetic_text": TEXT,
                "format": "pcm_s16le", "sample_rate": 24000, "channels": 1,
                "bytes": len(pcm), "duration_seconds": round(len(pcm) / 48000, 3),
                "http_chunks": chunks, "first_chunk_ms": first_ms, "completion_ms": elapsed,
                "peak_process_tree_rss_kib_observed": peak_rss,
                "peak_rss_mib_observed": round(peak_rss / 1024, 2),
                "abort_left_child_processes": children_after_abort,
                "post_abort_request_status": response.status_code,
                "sample_file": "sample.wav",
                "note": "Pipecat additionally buffers 24000B before its first frame; this is adapter latency only.",
            }
            (ROOT / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        stopped = True
        await task
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


if __name__ == "__main__":
    asyncio.run(main())
