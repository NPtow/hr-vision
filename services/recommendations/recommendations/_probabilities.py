"""Resolve the frozen shared validator in a release or a monorepo checkout.

Only the local pure probability helper is used. Importing this package does not
instantiate a provider client, read credentials or perform network requests.
"""

from importlib import import_module, util
from pathlib import Path
import sys


def _shared_client():
    try:
        return import_module("jev_client")
    except ModuleNotFoundError as error:
        if error.name != "jev_client":
            raise
    source = Path(__file__).resolve()
    candidates = [source.parents[1] / "jev_client/__init__.py"]
    if len(source.parents) > 3:
        candidates.append(source.parents[3] / "packages/jev-client/jev_client/__init__.py")
    for path in candidates:
        if not path.is_file():
            continue
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
        "Shared probability validator missing: export packages/jev-client into this release.",
        name="jev_client")


is_choice_probability_distribution = _shared_client().is_choice_probability_distribution
