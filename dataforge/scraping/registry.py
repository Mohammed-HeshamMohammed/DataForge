"""A name-to-class registry so spiders can be selected from the CLI and web UI."""

from __future__ import annotations

from dataforge.scraping.base import Spider

_REGISTRY: dict[str, type[Spider]] = {}


def register_spider(cls: type[Spider]) -> type[Spider]:
    """Class decorator registering a spider under its ``name`` attribute."""
    name = getattr(cls, "name", "")
    if not name:
        raise ValueError(f"{cls.__name__} must define a non-empty 'name'")
    if name in _REGISTRY and _REGISTRY[name] is not cls:
        raise ValueError(f"Spider name '{name}' is already registered")
    _REGISTRY[name] = cls
    return cls


def get_spider(name: str) -> type[Spider]:
    """Look up a registered spider class by name."""
    _load_builtin_spiders()
    if name not in _REGISTRY:
        raise KeyError(f"Unknown spider '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def list_spiders() -> dict[str, str]:
    """Return ``{name: description}`` for every registered spider."""
    _load_builtin_spiders()
    return {name: cls.description for name, cls in sorted(_REGISTRY.items())}


def _load_builtin_spiders() -> None:
    """Import the bundled spider modules so their registrations run."""
    from dataforge.scraping import spiders  # noqa: F401
