import importlib

import pytest

import can
from can.interface import _get_class_for_interface
from can.interfaces import BACKENDS, bmcan


def test_bmcan_backend_is_registered() -> None:
    assert BACKENDS["bmcan"] == ("can.interfaces.bmcan", "BmCanBus")


def test_bm_error_keeps_error_code() -> None:
    error = bmcan.BmOperationError(42, "operation failed", "BM_Test")

    assert error.bm_error_code == 42
    assert error.error_code == 42
    assert str(error) == "BM_Test failed (operation failed) [Error Code 42]"


def test_bmcan_backend_missing_library_reports_interface_error() -> None:
    if hasattr(bmcan, "BmCanBus"):
        pytest.skip("BMAPI is installed on this machine")

    with pytest.raises(can.CanInterfaceNotImplementedError):
        _get_class_for_interface("bmcan")


def test_send_error_recovers_without_raising_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(bmcan, "BmCanBus"):
        pytest.skip("BMAPI is not installed on this machine")

    canlib = importlib.import_module("can.interfaces.bmcan.canlib")
    bus = bmcan.BmCanBus.__new__(bmcan.BmCanBus)
    bus._handle = object()
    bus._raise_on_send_error = False
    recover_calls = []

    def raise_send_error(*_args: object) -> None:
        raise bmcan.BmError(
            canlib.bmapi.BM_ERROR_ILLOPERATION, "BM_ERROR_ILLOPERATION", "BM_Write"
        )

    monkeypatch.setattr(canlib.bmapi, "BM_WriteCanMessage", raise_send_error)
    monkeypatch.setattr(bus, "recover_from_error", lambda: recover_calls.append(True))

    msg = can.Message(arbitration_id=0x123, data=[0x11], is_extended_id=False)
    bus.send(msg, timeout=1.0)

    assert recover_calls == [True]


def test_send_error_can_raise_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(bmcan, "BmCanBus"):
        pytest.skip("BMAPI is not installed on this machine")

    canlib = importlib.import_module("can.interfaces.bmcan.canlib")
    bus = bmcan.BmCanBus.__new__(bmcan.BmCanBus)
    bus._handle = object()
    bus._raise_on_send_error = True
    recover_calls = []

    def raise_send_error(*_args: object) -> None:
        raise bmcan.BmError(
            canlib.bmapi.BM_ERROR_ILLOPERATION, "BM_ERROR_ILLOPERATION", "BM_Write"
        )

    monkeypatch.setattr(canlib.bmapi, "BM_WriteCanMessage", raise_send_error)
    monkeypatch.setattr(bus, "recover_from_error", lambda: recover_calls.append(True))

    msg = can.Message(arbitration_id=0x123, data=[0x11], is_extended_id=False)
    with pytest.raises(bmcan.BmOperationError):
        bus.send(msg, timeout=1.0)

    assert recover_calls == [True]
