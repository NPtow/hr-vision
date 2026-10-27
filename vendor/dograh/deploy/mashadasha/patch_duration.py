"""Keep the Oasis hour limit, recording capacity and concurrency lease consistent.

Run against the pinned API image. Refuse unknown upstream source rather than
silently leaving a half-applied duration change.
"""
import hashlib
from pathlib import Path


RECORDING_SOURCE_SHA256 = "5b5e8e50cdfdd5ce8243b41da931e97b5bd4d3fdb95d7aca969291848a21079d"


def patch(root: Path) -> None:
    replacements = {
        "api/schemas/workflow_configurations.py": (
            "MAX_CALL_DURATION_SECONDS = 1200",
            "MAX_CALL_DURATION_SECONDS = 3600",
        ),
        "api/services/call_concurrency/rate_limiter.py": (
            "self.stale_call_timeout = 1200",
            "self.stale_call_timeout = 3900",
        ),
        "api/services/pipecat/in_memory_buffers.py": (
            "self._max_size = 100 * 1024 * 1024  # 100MB limit",
            "self._max_size = 128 * 1024 * 1024  # One hour of 16kHz mono PCM16 per track",
        ),
    }
    changes = []
    for filename, (before, after) in replacements.items():
        path = root / filename
        source = path.read_text()
        if filename == "api/services/pipecat/in_memory_buffers.py":
            original = source.replace(after, before, 1)
            if hashlib.sha256(original.encode()).hexdigest() != RECORDING_SOURCE_SHA256:
                raise RuntimeError(f"Unsupported upstream recording code: {filename}")
        if after in source and before not in source:
            continue
        if source.count(before) != 1:
            raise RuntimeError(f"Unsupported upstream duration code: {filename}")
        changes.append((path, source.replace(before, after, 1)))
    for path, source in changes:
        path.write_text(source)


if __name__ == "__main__":
    import sys
    patch(Path(sys.argv[1] if len(sys.argv) > 1 else "/app"))
