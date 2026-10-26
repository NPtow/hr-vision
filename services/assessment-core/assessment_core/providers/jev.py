"""Compatibility import for the shared, frozen-in-release JEV transport.

Both import paths refer to the same module, including monkeypatch targets.
The candidate service keeps its historical PYTHONPATH in a monorepo checkout
and in an exported release. No candidate process is needed by other services.
"""
from importlib import import_module, util
from pathlib import Path
import sys


def _shared_transport():
    try:
        return import_module("jev_client")
    except ModuleNotFoundError as error:
        if error.name != "jev_client":
            raise
    source = Path(__file__).resolve()
    # An immutable release freezes the package beside assessment_core. The
    # second location supports the historical monorepo-only PYTHONPATH.
    candidates = [source.parents[2] / "jev_client/__init__.py"]
    if len(source.parents) > 4:
        candidates.append(source.parents[4] / "packages/jev-client/jev_client/__init__.py")
    for path in candidates:
        if path.is_file():
            spec = util.spec_from_file_location("jev_client", path,
                submodule_search_locations=[str(path.parent)])
            module = util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(spec.name, None)
                raise
            return module
    raise ModuleNotFoundError(
        "JEV transport is missing: export packages/jev-client into this release.",
        name="jev_client")


_shared = _shared_transport()
sys.modules[__name__] = _shared
# The release installer also executes this file directly with importlib and
# retains the original module object. Keep that caller compatible as well.
globals().update(_shared.__dict__)
