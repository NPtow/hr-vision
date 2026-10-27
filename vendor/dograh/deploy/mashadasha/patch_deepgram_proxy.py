"""Build-time, SHA-guarded fix for the pinned Deepgram SDK async transport."""

import hashlib
from pathlib import Path
import shutil
import sys
import sysconfig


EXPECTED_SHA256 = "ede27c48b12a1e30191b45d046d4e567f5fa784944f4eb772e372293f99a5b54"
ORIGINAL = '''try:
    from websockets.legacy.client import connect as websockets_client_connect  # type: ignore
except ImportError:
    from websockets import connect as websockets_client_connect  # type: ignore
'''
REPLACEMENT = "from ._dograh_proxy_transport import connect as websockets_client_connect\n"


def patch(client_file: Path, wrapper_file: Path) -> None:
    source = client_file.read_bytes()
    if hashlib.sha256(source).hexdigest() != EXPECTED_SHA256:
        raise RuntimeError("Deepgram listen.v1 source changed; review the transport patch")
    text = source.decode()
    if text.count(ORIGINAL) != 1:
        raise RuntimeError("Deepgram transport import did not match exactly once")
    updated = text.replace(ORIGINAL, REPLACEMENT)
    compile(updated, str(client_file), "exec")
    compile(wrapper_file.read_text(), str(wrapper_file), "exec")
    shutil.copyfile(wrapper_file, client_file.with_name("_dograh_proxy_transport.py"))
    client_file.write_text(updated)


if __name__ == "__main__":
    target = Path(sysconfig.get_paths()["purelib"]) / "deepgram/listen/v1/client.py"
    patch(target, Path(sys.argv[1]))
