"""Hardware-independent tests for the bmcan periodic transmit backend."""

import ctypes
import gc
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import can
from can.broadcastmanager import (
    ModifiableCyclicTaskABC,
    RestartableCyclicTaskABC,
    ThreadBasedCyclicSendTask,
)
from can.interfaces.bmcan import canlib


class FakeTxTask(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint8),
        ("version", ctypes.c_uint8),
        ("flags", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("e2e", ctypes.c_uint8),
        ("delay", ctypes.c_uint16),
        ("nrounds", ctypes.c_uint16),
        ("cycle", ctypes.c_uint16),
        ("nmessages", ctypes.c_uint16),
        ("id", ctypes.c_uint32),
        ("payload", ctypes.c_uint8 * 64),
    ]


@pytest.fixture()
def mocked_bmapi(monkeypatch: pytest.MonkeyPatch):
    control = MagicMock(return_value=0)
    monkeypatch.setattr(canlib.bmapi, "BM_TxTaskTypeDef", FakeTxTask)
    monkeypatch.setattr(canlib.bmapi, "BM_Control", control)
    monkeypatch.setattr(canlib.bmapi, "BM_ClearBuffer", MagicMock(return_value=0))
    monkeypatch.setattr(canlib.bmapi, "BM_Close", MagicMock(return_value=0))
    monkeypatch.setattr(canlib.bmapi, "BM_ChannelHandle", MagicMock(return_value=None))
    yield control
    gc.collect()


def make_bus(ntxtask: int = 64) -> canlib.BmCanBus:
    bus = canlib.BmCanBus.__new__(canlib.BmCanBus)
    bus._ntxtask = ntxtask
    bus._handle = object()
    bus._channelinfo = SimpleNamespace(port=0)
    bus._periodic_tasks = []
    bus._txtask_lock = threading.Lock()
    bus._txtask_slots = [None] * ntxtask
    bus._shutdown_lock = threading.Lock()
    bus._lock = threading.Lock()
    bus._is_shutdown = False
    bus.bus_list = [bus]
    bus.send = MagicMock()
    return bus


def make_msg(
    arbitration_id: int = 0x100,
    data: tuple[int, ...] = (1, 2, 3, 4),
    *,
    is_extended_id: bool = False,
):
    return can.Message(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=is_extended_id,
    )


def test_autostart_false_allocates_no_hardware_slot(mocked_bmapi: MagicMock) -> None:
    bus = make_bus()
    tasks = [
        bus.send_periodic(make_msg(0x100 + index), 0.1, autostart=False)
        for index in range(100)
    ]

    assert all(isinstance(task, canlib.BmCanTaskWrapper) for task in tasks)
    assert all(isinstance(task, RestartableCyclicTaskABC) for task in tasks)
    assert all(isinstance(task, ModifiableCyclicTaskABC) for task in tasks)
    assert all(task._txtask_index == -1 for task in tasks)
    assert bus._txtask_slots == [None] * 64
    mocked_bmapi.assert_not_called()


def test_slots_are_released_and_reused(mocked_bmapi: MagicMock) -> None:
    bus = make_bus()
    tasks = [
        bus.send_periodic(make_msg(0x100 + index), 0.1, autostart=False)
        for index in range(65)
    ]

    for task in tasks[:64]:
        task.start()
    with pytest.raises(can.CanOperationError):
        tasks[64].start()

    released_index = tasks[10]._txtask_index
    tasks[10].stop()
    tasks[10].stop()
    tasks[64].start()

    assert tasks[64]._txtask_index == released_index
    assert all(call.args[2] >= 0 for call in mocked_bmapi.call_args_list)


def test_modifier_callback_uses_official_software_task(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    callback = MagicMock()

    task = bus.send_periodic(
        make_msg(), 0.1, autostart=False, modifier_callback=callback
    )

    assert isinstance(task, ThreadBasedCyclicSendTask)
    assert task.modifier_callback is callback
    mocked_bmapi.assert_not_called()


def test_modify_data_updates_dormant_and_running_task(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)

    task.modify_data(make_msg(data=(9, 8)))
    assert list(task._bmtxtask.payload[:4]) == [9, 8, 0, 0]
    mocked_bmapi.assert_not_called()

    task.start()
    mocked_bmapi.reset_mock()
    task.modify_data(make_msg(data=(7, 6, 5)))
    assert list(task._bmtxtask.payload[:4]) == [7, 6, 5, 0]
    mocked_bmapi.assert_called_once()


def test_modify_data_rejects_arbitration_id_change(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)

    with pytest.raises(ValueError):
        task.modify_data(make_msg(0x200))


@pytest.mark.parametrize(
    ("initial_extended", "modified_extended", "expected_id"),
    [(False, True, 0x91800), (True, False, 0x123)],
)
def test_modify_data_reencodes_standard_and_extended_id(
    mocked_bmapi: MagicMock,
    initial_extended: bool,
    modified_extended: bool,
    expected_id: int,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(
        make_msg(0x123, is_extended_id=initial_extended),
        0.1,
        autostart=False,
    )

    task.modify_data(make_msg(0x123, is_extended_id=modified_extended))

    assert task._bmtxtask.id == expected_id
    assert bool(task._bmtxtask.flags & canlib.bmapi.BM_MESSAGE_FLAGS_IDE) is (
        modified_extended
    )


def test_duration_uses_software_task_and_no_hardware_slot(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.001, duration=0.01)

    task.thread.join(timeout=1.0)

    assert isinstance(task, ThreadBasedCyclicSendTask)
    assert task.stopped
    assert bus._txtask_slots == [None] * 64
    mocked_bmapi.assert_not_called()


def test_concurrent_start_and_stop_same_task_never_leaks_slot(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)

    for _ in range(200):
        barrier = threading.Barrier(3)

        def start() -> None:
            barrier.wait()
            task.start()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(start) for _ in range(2)]
            barrier.wait()
            for future in futures:
                future.result()
        assert bus._txtask_slots.count(task) == 1

        barrier = threading.Barrier(3)

        def stop() -> None:
            barrier.wait()
            task.stop()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(stop) for _ in range(2)]
            barrier.wait()
            for future in futures:
                future.result()
        assert task._txtask_index == -1
        assert task not in bus._txtask_slots

    assert all(call.args[2] >= 0 for call in mocked_bmapi.call_args_list)


def test_concurrent_start_of_100_tasks_has_unique_slots(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus(ntxtask=100)
    tasks = [
        bus.send_periodic(make_msg(index), 0.1, autostart=False) for index in range(100)
    ]

    with ThreadPoolExecutor(max_workers=32) as executor:
        list(executor.map(lambda task: task.start(), tasks))

    assert sorted(task._txtask_index for task in tasks) == list(range(100))
    assert len({id(task) for task in bus._txtask_slots}) == 100


def test_start_failure_rolls_back_reserved_slot(mocked_bmapi: MagicMock) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)
    mocked_bmapi.side_effect = RuntimeError("start failed")

    with pytest.raises(RuntimeError, match="start failed"):
        task.start()

    assert task._txtask_index == -1
    assert bus._txtask_slots == [None] * 64


def test_modify_failure_preserves_previous_task(mocked_bmapi: MagicMock) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(data=(1, 2)), 0.1)
    previous_task = task._bmtxtask
    previous_messages = task.messages
    mocked_bmapi.side_effect = RuntimeError("modify failed")

    with pytest.raises(RuntimeError, match="modify failed"):
        task.modify_data(make_msg(data=(9, 9)))

    assert task._bmtxtask is previous_task
    assert task.messages == previous_messages
    assert task._txtask_index == 0
    assert bus._txtask_slots[0] is task


def test_stop_failure_keeps_slot_for_retry(mocked_bmapi: MagicMock) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1)
    mocked_bmapi.side_effect = RuntimeError("stop failed")

    with pytest.raises(RuntimeError, match="stop failed"):
        task.stop()

    assert task._txtask_index == 0
    assert bus._txtask_slots[0] is task
    mocked_bmapi.side_effect = None
    task.stop()
    assert task._txtask_index == -1
    assert bus._txtask_slots[0] is None


def test_ten_thousand_start_stop_modify_cycles(mocked_bmapi: MagicMock) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)

    for index in range(10_000):
        task.start()
        task.modify_data(make_msg(data=(index & 0xFF,)))
        task.stop()

    assert task._txtask_index == -1
    assert bus._txtask_slots == [None] * 64
    assert all(call.args[2] >= 0 for call in mocked_bmapi.call_args_list)


def test_concurrent_start_stop_and_modify_preserves_registry(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)

    for index in range(200):
        barrier = threading.Barrier(4)

        def run(operation) -> None:
            barrier.wait()
            operation()

        operations = (
            task.start,
            task.stop,
            lambda: task.modify_data(make_msg(data=(index & 0xFF,))),
        )
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(run, operation) for operation in operations]
            barrier.wait()
            for future in futures:
                future.result()

        assert bus._txtask_slots.count(task) in (0, 1)
        assert task._txtask_index >= -1

    task.stop()
    assert task._txtask_index == -1
    assert task not in bus._txtask_slots
    assert all(call.args[2] >= 0 for call in mocked_bmapi.call_args_list)


def test_concurrent_shutdown_is_idempotent_and_stops_tasks(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    tasks = [bus.send_periodic(make_msg(index), 0.1) for index in range(32)]
    barrier = threading.Barrier(9)

    def shutdown() -> None:
        barrier.wait()
        bus.shutdown()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(shutdown) for _ in range(8)]
        barrier.wait()
        for future in futures:
            future.result()

    assert bus._is_shutdown
    assert bus._handle is None
    assert bus._periodic_tasks == []
    assert bus._txtask_slots == [None] * 64
    assert all(task._txtask_index == -1 for task in tasks)
    canlib.bmapi.BM_Close.assert_called_once()
    canlib.bmapi.BM_ClearBuffer.assert_called_once()


def test_start_after_shutdown_is_rejected_without_allocating_slot(
    mocked_bmapi: MagicMock,
) -> None:
    bus = make_bus()
    task = bus.send_periodic(make_msg(), 0.1, autostart=False)
    bus.shutdown()

    with pytest.raises(can.CanOperationError, match="shut down bus"):
        task.start()

    assert task._txtask_index == -1
    assert bus._txtask_slots == [None] * 64
