"""Discover and register SystemDesk tool modules."""

import importlib
import pkgutil


def load_tools() -> None:
    """Import public tool modules in deterministic order to register their tools."""
    modules = sorted(pkgutil.iter_modules(__path__), key=lambda module: module.name)
    for module in modules:
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{module.name}")
