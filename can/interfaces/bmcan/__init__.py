"""BUSMUST BMAPI interface."""

from .exceptions import BmError, BmInitializationError, BmOperationError

try:
    from .canlib import BmCanBus
except Exception as exc:  # pragma: no cover - depends on external BMAPI installation
    _bmcan_import_error = exc

__all__ = [
    "BmCanBus",
    "BmError",
    "BmInitializationError",
    "BmOperationError",
]
