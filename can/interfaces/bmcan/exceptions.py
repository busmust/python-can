"""Exception declarations for the BUSMUST BMAPI interface."""

from typing import Any

from can import CanError, CanInitializationError, CanOperationError


class BmError(CanError):
    def __init__(
        self, bm_error_code: int | None, error_string: str, function: str
    ) -> None:
        self.bm_error_code = bm_error_code
        super().__init__(
            message=f"{function} failed ({error_string})", error_code=bm_error_code
        )
        self._args = bm_error_code, error_string, function

    def __reduce__(self) -> str | tuple[Any, ...]:
        return type(self), self._args, {}


class BmInitializationError(BmError, CanInitializationError):
    @staticmethod
    def from_generic(error: BmError) -> "BmInitializationError":
        return BmInitializationError(*error._args)


class BmOperationError(BmError, CanOperationError):
    @staticmethod
    def from_generic(error: BmError) -> "BmOperationError":
        return BmOperationError(*error._args)
