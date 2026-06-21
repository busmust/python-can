# mypy: ignore-errors
"""BUSMUST BMAPI interface."""

import ctypes
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from can import BusABC, CanProtocol, Message
from can.broadcastmanager import CyclicSendTaskABC
from can.bus import BusState
from can.util import dlc2len, len2dlc

from .exceptions import BmError, BmInitializationError, BmOperationError

LOG = logging.getLogger(__name__)


def set_highest_thread_priority() -> bool:
    if sys.platform != "win32":
        return False

    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    GetCurrentThread = kernel32.GetCurrentThread
    GetCurrentThread.argtypes = []
    GetCurrentThread.restype = wintypes.HANDLE

    SetThreadPriority = kernel32.SetThreadPriority
    SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    SetThreadPriority.restype = wintypes.BOOL

    THREAD_PRIORITY_TIME_CRITICAL = 15

    thread_handle = GetCurrentThread()
    return SetThreadPriority(thread_handle, THREAD_PRIORITY_TIME_CRITICAL)


try:
    # Try builtin Python 3 Windows API
    from _winapi import INFINITE, WaitForSingleObject

    HAS_EVENTS = True
except ImportError:
    try:
        # Try pywin32 package
        from win32event import INFINITE, WaitForSingleObject

        HAS_EVENTS = True
    except ImportError:
        # Use polling instead
        INFINITE = -1
        HAS_EVENTS = False

bmapi = None
try:
    from . import bmapi
except Exception as exc:
    LOG.debug("Could not import bmapi, please check your BMAPI path: %s", exc)
    raise exc


class BmCanTaskWrapper(CyclicSendTaskABC):
    """Hardware cyclic transmit task backed by a BMAPI TX task entry."""

    def __init__(self, bus: "BmCanBus", msg: Message, period: float):
        super().__init__(msg, period)
        self._bus = bus
        self._txtask_index = -1
        self._bmtxtask: bmapi.BM_TxTaskTypeDef | None = None

    def start(self) -> bool:
        assert self._txtask_index == -1, "Task is already started"
        index = self.get_first_free_txtask_index()
        if index == -1:
            return False
        assert self._bmtxtask is not None
        self._bmtxtask.type = bmapi.BM_TXTASK_FIXED
        self._txtask_index = index
        command = bmapi.BM_CAN_TXTASK_TABLE | bmapi.BM_CAN_CTRL_WR
        bmapi.BM_Control(
            self._bus._handle,
            command,
            index,
            self._bus._channelinfo.port,
            ctypes.byref(self._bmtxtask),
            ctypes.sizeof(self._bmtxtask),
        )
        return True

    def stop(self) -> None:
        if self._txtask_index < 0 or self._bmtxtask is None:
            return
        self._bmtxtask.type = bmapi.BM_TXTASK_INVALID
        command = bmapi.BM_CAN_TXTASK_TABLE | bmapi.BM_CAN_CTRL_WR
        bmapi.BM_Control(
            self._bus._handle,
            command,
            self._txtask_index,
            self._bus._channelinfo.port,
            ctypes.byref(self._bmtxtask),
            ctypes.sizeof(self._bmtxtask),
        )
        self._txtask_index = -1

    def get_first_free_txtask_index(self) -> int:
        for i in range(self._bus._ntxtask):
            if all(
                not isinstance(task, BmCanTaskWrapper) or i != task._txtask_index
                for task in self._bus._periodic_tasks
            ):
                return i
        return -1


class BmCanBus(BusABC):
    """The CAN Bus implemented for the BUSMUST USB-CAN interface."""

    __initialized = False
    bus_list: list["BmCanBus"] = []

    @classmethod
    def __init_class__(cls) -> None:
        if bmapi is None:
            raise ImportError("The BMAPI has not been loaded")
        if not BmCanBus.__initialized:
            bmapi.BM_Init()
            bmapi.BM_SetLogLevel(bmapi.BM_LOG_ERR)
            BmCanBus.__initialized = True

    def __init__(
        self,
        channel: int | str,
        fd: bool = True,
        receive_own_messages: bool = False,
        listen_only: bool = False,
        bitrate: int = 500000,
        data_bitrate: int = 500000,
        samplepos: int = 75,
        data_samplepos: int = 75,
        tres: bool = False,
        can_filters: Sequence[dict[str, Any]] | None = None,
        remote_ip: bytes | str | None = None,
        remote_enumeration_timeout: int = 100,
        raise_on_send_error: bool = False,
        debug: bool = False,
        **kwargs: Any,
    ):
        """
        :param int channel:
            The channel index to create this bus with, which is the index to all available ports when enumerating Busmust devices.
            Can also be a string of the channel's full name. i.e. "BM-CANFD-X1-PRO(1234) CH1"
        :param bool fd:
            If CAN-FD frames should be supported.
        :param bool receive_own_messages:
            If Loopback mode should be supported.
        :param bool listen_only:
            If Listen only mode should be supported, this is the same as setting 'state' property to INACTIVE.
        :param int bitrate:
            Bitrate in bits/s.
        :param int data_bitrate:
            Which bitrate to use for data phase in CAN FD.
            Defaults to arbitration bitrate.
        :param int samplepos:
            Sample position (%).
        :param int data_samplepos:
            Data phase sample pos (%) in CAN FD.
        :param bool tres:
            If 120Ohm CAN terminal register should be enabled.
        :param bool raise_on_send_error:
            Raise BMAPI send errors after running the built-in recovery check.
            Defaults to ``False`` for compatibility with the BUSMUST BMAPI SDK
            python-can 4.0.0 backend, which absorbs send errors after recovery.
        """
        if debug:
            debug_log = logging.getLogger(__name__)
            debug_log.propagate = False
            log_dir = os.path.dirname("bmcan_bus.log")
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            if debug_log.handlers:
                for handler in debug_log.handlers:
                    debug_log.removeHandler(handler)
            file_handler = logging.FileHandler(
                filename="bmcan_bus.log", mode="a", encoding="utf-8"
            )
            log_format = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            file_handler.setFormatter(log_format)
            file_handler.setLevel(logging.INFO)
            debug_log.addHandler(file_handler)
            debug_log.setLevel(logging.INFO)
            self._log = debug_log
        self._debug = debug
        self._raise_on_send_error = raise_on_send_error
        try:
            BmCanBus.__init_class__()
        except BmError as exc:
            raise BmInitializationError.from_generic(exc) from exc
        set_highest_thread_priority()
        self._bmapi = bmapi  # Enable external access

        infolist = bmapi.BM_ChannelInfoListTypeDef()
        numOfInfo = ctypes.c_int(len(infolist.entries))
        try:
            if remote_ip is None:
                bmapi.BM_EnumerateByCap(
                    ctypes.byref(infolist), ctypes.byref(numOfInfo), bmapi.BM_CAN_CAP
                )
            else:
                remote_ip_obj = (ctypes.c_uint8 * 4)()
                if isinstance(remote_ip, bytes):
                    for i in range(4):
                        remote_ip_obj[i] = remote_ip[i]
                elif isinstance(remote_ip, str):
                    parts = remote_ip.split(".")
                    for i in range(4):
                        remote_ip_obj[i] = int(parts[i])
                else:
                    raise BmInitializationError(
                        bmapi.BM_ERROR_NODRIVER,
                        "Channel remote address format error: " + str(remote_ip),
                        "BmCanBus.__init__",
                    )
                bmapi.BM_EnumerateRemote(
                    ctypes.byref(infolist),
                    ctypes.byref(numOfInfo),
                    remote_ip_obj,
                    remote_enumeration_timeout,
                )
        except BmError as exc:
            raise BmInitializationError.from_generic(exc) from exc

        if isinstance(channel, int):
            if channel < numOfInfo.value:
                self._channelinfo = infolist.entries[channel]
            else:
                raise BmInitializationError(
                    bmapi.BM_ERROR_NODRIVER,
                    "Channel %d is not connected or is in use by another app."
                    % channel,
                    "BmCanBus.__init__",
                )
        elif isinstance(channel, str):
            for info in infolist.entries:
                if info.name.decode() == channel:
                    self._channelinfo = info
                    break
            else:
                raise BmInitializationError(
                    bmapi.BM_ERROR_NODRIVER,
                    "Channel %s is not connected or is in use by another app."
                    % channel,
                    "BmCanBus.__init__",
                )
        else:
            raise ValueError("channel must be an integer index or channel name")

        self._mode = bmapi.BM_CAN_NORMAL_MODE
        if not fd:
            self._mode = bmapi.BM_CAN_CLASSIC_MODE
        elif receive_own_messages:
            self._mode = bmapi.BM_CAN_EXTERNAL_LOOPBACK_MODE
        elif listen_only:
            self._mode = bmapi.BM_CAN_LISTEN_ONLY_MODE

        self._tres = bmapi.BM_TRESISTOR_DISABLED
        if tres:
            self._tres = bmapi.BM_TRESISTOR_120

        self._bitrate = bmapi.BM_BitrateTypeDef()
        self._bitrate.nbitrate = int(bitrate / 1000)
        self._bitrate.dbitrate = int(data_bitrate / 1000)
        self._bitrate.nsamplepos = samplepos
        self._bitrate.dsamplepos = data_samplepos

        self._handle = bmapi.BM_ChannelHandle()
        try:
            bmapi.BM_OpenEx(
                ctypes.byref(self._handle),
                ctypes.byref(self._channelinfo),
                self._mode,
                self._tres,
                ctypes.byref(self._bitrate),
                ctypes.cast(
                    ctypes.c_void_p(), ctypes.POINTER(bmapi.BM_RxFilterListTypeDef)
                ),
                0,
            )
        except BmError as exc:
            raise BmInitializationError.from_generic(exc) from exc

        super().__init__(channel=channel, can_filters=can_filters, **kwargs)
        self._can_protocol = CanProtocol.CAN_FD if fd else CanProtocol.CAN_20
        timestamp_ns = int(time.time() * 1000000000)
        try:
            bmapi.BM_SetPtpTime(self._handle, ctypes.c_uint64(timestamp_ns))
        except BmError:
            LOG.debug("%s does not support PTP", self._channelinfo.name.decode())
        self.bus_list.append(self)
        self.channel_info = self._channelinfo.name.decode()

        startTimestamp = ctypes.c_uint32()
        bmapi.BM_GetTimestamp(self._handle, ctypes.byref(startTimestamp))
        _time = time.time()
        self._start_time = _time

        self._previous_recovery_ts = _time

        self._notification = bmapi.BM_NotificationHandle()
        bmapi.BM_GetNotification(self._handle, ctypes.byref(self._notification))

        time.sleep(0.05)

        self._state = BusState.ACTIVE
        ntxtask = ctypes.c_int(0)
        self._isotp_config = bmapi.BM_IsotpConfigTypeDef()
        try:
            result = bmapi.BM_Control(
                self._handle,
                bmapi.BM_GET_STAT,
                bmapi.BM_STAT_MAX_TXTASK,
                self._channelinfo.port,
                ctypes.byref(ntxtask),
                4,
            )
            if result > 0:
                buf = ctypes.create_string_buffer(256)
                bmapi.BM_GetErrorText(result, buf, len(buf), 0)
                raise BmError(result, buf.value.decode(), "BmCanBus.__init__")
        except BmError as e:
            ntxtask.value = 0
        self._ntxtask = ntxtask.value

        bmtxtask = bmapi.BM_TxTaskTypeDef()
        for index in range(self._ntxtask):
            bmtxtask.type = bmapi.BM_TXTASK_INVALID
            command = bmapi.BM_CAN_TXTASK_TABLE | bmapi.BM_CAN_CTRL_WR
            bmapi.BM_Control(
                self._handle,
                command,
                index,
                self._channelinfo.port,
                ctypes.byref(bmtxtask),
                ctypes.sizeof(bmtxtask),
            )
        self._lock = threading.Lock()

    def get_open_time(self):
        return self._start_time

    def sync_timestamp(self):
        pass

    def _send_periodic_internal(
        self,
        msgs: Sequence[Message] | Message,
        period: float,
        duration: float | None = None,
        autostart: bool = True,
        modifier_callback: Callable[[Message], None] | None = None,
    ):
        if isinstance(msgs, Message):
            messages = [msgs]
        else:
            messages = list(msgs)

        if (
            self._ntxtask <= 0
            or not autostart
            or modifier_callback is not None
            or len(messages) != 1
        ):
            return super()._send_periodic_internal(
                msgs, period, duration, autostart, modifier_callback
            )

        msg = messages[0]
        bmtxtask = bmapi.BM_TxTaskTypeDef()
        txtask = BmCanTaskWrapper(self, msg, period)
        bmtxtask.type = bmapi.BM_TXTASK_FIXED
        bmtxtask.version = 1
        bmtxtask.flags |= bmapi.BM_MESSAGE_FLAGS_IDE if msg.is_extended_id else 0
        bmtxtask.flags |= bmapi.BM_MESSAGE_FLAGS_RTR if msg.is_remote_frame else 0
        bmtxtask.flags |= bmapi.BM_MESSAGE_FLAGS_FDF if msg.is_fd else 0
        bmtxtask.flags |= bmapi.BM_MESSAGE_FLAGS_BRS if msg.bitrate_switch else 0
        bmtxtask.flags |= bmapi.BM_MESSAGE_FLAGS_ESI if msg.error_state_indicator else 0
        bmtxtask.length = msg.dlc
        bmtxtask.e2e = 0
        cycle = round(period * 1000)
        bmtxtask.delay = 0
        if duration is not None:
            nrounds = round(duration / period)
            bmtxtask.nrounds = nrounds if nrounds < 0xFFFF else 0xFFFF - 1
        else:
            bmtxtask.nrounds = 0xFFFF
        bmtxtask.cycle = cycle
        bmtxtask.nmessages = 1
        bmtxtask.id = (
            ((msg.arbitration_id << 18) & 0x7FF)
            | ((msg.arbitration_id) & 0x3FFFF) << 11
            if msg.is_extended_id
            else msg.arbitration_id
        )
        for i in range(bmtxtask.length):
            bmtxtask.payload[i] = msg.data[i]
        txtask._bmtxtask = bmtxtask
        succeed = txtask.start()
        if not succeed:
            return super()._send_periodic_internal(
                msgs, period, duration, autostart, modifier_callback
            )
        return txtask

    def stop_all_periodic_tasks(self, remove_tasks: bool = True) -> None:
        """Stop sending any messages that were started using **bus.send_periodic**.

        .. note::
            The result is undefined if a single task throws an exception while being stopped.

        :param bool remove_tasks:
            Stop tracking the stopped tasks.
        """
        super().stop_all_periodic_tasks(remove_tasks)
        with self._lock:
            bmapi.BM_ClearBuffer(self._handle)

    def add_periodic_txtask(self, txtask):
        self._periodic_tasks.append(txtask)

    def del_periodic_txtask(self, txtask):
        self._periodic_tasks.remove(txtask)

    def _apply_filters(self, filters):
        if filters:
            # Only up to one filter per ID type allowed
            if len(filters) == 1 or (
                len(filters) == 2
                and filters[0].get("extended") != filters[1].get("extended")
            ):
                bmfilters = bmapi.BM_RxFilterListTypeDef()
                try:
                    for i in range(len(filters)):
                        can_filter = filters[i]
                        bmfilters.entries[i].type = bmapi.BM_RXFILTER_BASIC
                        bmfilters.entries[i].flags_mask = bmapi.BM_MESSAGE_FLAGS_IDE
                        if can_filter.get("extended"):
                            bmfilters.entries[i].flags_value = (
                                bmapi.BM_MESSAGE_FLAGS_IDE
                            )
                            bmfilters.entries[i].id_mask = (
                                can_filter["can_mask"] >> 18
                            ) | ((can_filter["can_mask"] & 0x3FFFF) << 11)
                            bmfilters.entries[i].id_value = (
                                can_filter["can_id"] >> 18
                            ) | ((can_filter["can_id"] & 0x3FFFF) << 11)
                        else:
                            bmfilters.entries[i].flags_value = 0
                            bmfilters.entries[i].id_mask = can_filter["can_mask"]
                            bmfilters.entries[i].id_value = can_filter["can_id"]
                    bmapi.BM_SetRxFilters(
                        self._handle, ctypes.byref(bmfilters), len(bmfilters.entries)
                    )
                    time.sleep(0.05)
                except BmError as exc:
                    LOG.warning("Could not set filters: %s", exc)
                    # go to fallback
                else:
                    self._is_filtered = True
                    return
            else:
                LOG.warning("Only up to one filter per extended or standard ID allowed")
                # go to fallback

        # fallback: reset filters
        self._is_filtered = False
        try:
            bmfilters = bmapi.BM_RxFilterListTypeDef()  # Default as invalid
            # Filter 0: allow all messages to pass
            bmfilters.entries[0].type = bmapi.BM_RXFILTER_BASIC
            bmfilters.entries[0].flags_mask = 0
            bmfilters.entries[0].flags_value = 0
            bmfilters.entries[0].id_mask = 0
            bmfilters.entries[0].id_value = 0
            bmapi.BM_SetRxFilters(self._handle, ctypes.byref(bmfilters), 2)
            time.sleep(0.05)
        except BmError as exc:
            LOG.warning("Could not reset filters: %s", exc)

    def _recv_internal(self, timeout):
        end_time = time.time() + timeout if timeout is not None else None

        bmmsg = bmapi.BM_DataTypeDef()
        channel = ctypes.c_uint32()
        timestamp = ctypes.c_uint32()
        while True:
            try:
                if self._handle is not None:
                    bmapi.BM_Read(self._handle, ctypes.byref(bmmsg))
                    # bmapi.BM_ReadCanMessage(self._handle, ctypes.byref(bmmsg), ctypes.byref(channel), ctypes.byref(timestamp))
                else:
                    self.recover_from_error()
            except BmError as exc:
                if exc.bm_error_code != bmapi.BM_ERROR_QRCVEMPTY:
                    raise BmOperationError.from_generic(exc) from exc
            else:
                istx = bmmsg.header.isackdata()
                canmsg = bmmsg.getCanMessage()
                timestamp = bmmsg.timestamp
                msg_id = (
                    canmsg.mid.getExtendedId()
                    if canmsg.ctrl.rx.IDE
                    else canmsg.mid.getStandardId()
                )
                dlc = dlc2len(canmsg.ctrl.rx.DLC)
                utcts = ctypes.c_uint64(0)
                status = bmapi.BM_GetDataPtpTimestamp(
                    self._handle, ctypes.byref(bmmsg), ctypes.byref(utcts)
                )
                if bmapi.BM_ERROR_OK != status:
                    utcts.value = bmapi.BM_GetHostPtpTime()
                timestamp = utcts.value * 1e-9
                msg = Message(
                    timestamp=timestamp,
                    arbitration_id=msg_id & 0x1FFFFFFF,
                    is_extended_id=bool(canmsg.ctrl.rx.IDE),
                    is_remote_frame=bool(canmsg.ctrl.rx.RTR),
                    is_error_frame=bool(False),
                    is_fd=bool(canmsg.ctrl.rx.FDF),
                    error_state_indicator=bool(canmsg.ctrl.rx.ESI),
                    bitrate_switch=bool(canmsg.ctrl.rx.BRS),
                    dlc=dlc,
                    data=canmsg.payload[:dlc],
                    channel=None if istx > 0 else channel.value,
                )
                return msg, self._is_filtered

            if end_time is not None and time.time() > end_time:
                return None, self._is_filtered

            # Wait for receive event to occur
            if timeout is None:
                time_left_ms = INFINITE
            else:
                time_left = end_time - time.time()
                time_left_ms = max(0, int(time_left * 1000))
            bmapi.BM_WaitForNotifications(
                ctypes.byref(self._notification), 1, time_left_ms
            )

    @staticmethod
    def _to_bm_can_message(msg: Message | bmapi.BM_CanMessageTypeDef):
        if isinstance(msg, bmapi.BM_CanMessageTypeDef):
            return msg

        bmmsg = bmapi.BM_CanMessageTypeDef()
        if msg.is_extended_id:
            bmmsg.mid.SID = msg.arbitration_id >> 18
            bmmsg.mid.EID = msg.arbitration_id & 0x3FFFF
        else:
            bmmsg.mid.SID = msg.arbitration_id
            bmmsg.mid.EID = 0
        bmmsg.ctrl.tx.IDE = 1 if msg.is_extended_id else 0
        bmmsg.ctrl.tx.FDF = 1 if msg.is_fd else 0
        bmmsg.ctrl.tx.BRS = 1 if msg.bitrate_switch else 0
        bmmsg.ctrl.tx.RTR = 1 if msg.is_remote_frame else 0
        bmmsg.ctrl.tx.ESI = 1 if msg.error_state_indicator else 0
        bmmsg.ctrl.tx.DLC = len2dlc(msg.dlc)
        bmmsg.payload[: len(msg.data)] = msg.data
        return bmmsg

    def send(
        self, msg: Message | bmapi.BM_CanMessageTypeDef, timeout: float | None = None
    ) -> None:
        timeoutms = int(timeout * 1000) if timeout is not None else -1
        timestamp = ctypes.c_uint32()
        bmmsg = self._to_bm_can_message(msg)
        try:
            if self._handle is not None:
                bmapi.BM_WriteCanMessage(
                    self._handle,
                    ctypes.byref(bmmsg),
                    0,
                    timeoutms,
                    ctypes.byref(timestamp),
                )
            else:
                self.recover_from_error()
        except BmError as exc:
            self.recover_from_error()
            if self._raise_on_send_error:
                raise BmOperationError.from_generic(exc) from exc

    def send_multiple(self, msgs, timeout=None):
        if not msgs:
            return
        ntotalmsgs = len(msgs)
        nsentmsgs = 0
        nchunkmsgs = 10000
        bmmsgs = (bmapi.BM_CanMessageTypeDef * nchunkmsgs)()
        nullptr = ctypes.cast(0, ctypes.POINTER(ctypes.c_uint32))
        dataptr = ctypes.cast(bmmsgs, ctypes.POINTER(bmapi.BM_CanMessageTypeDef))
        timeoutms = int(timeout * 1000) if timeout is not None else -1
        while nsentmsgs < ntotalmsgs:
            nmsgs = min(nchunkmsgs, ntotalmsgs - nsentmsgs)
            if isinstance(msgs[0], bmapi.BM_CanMessageTypeDef):
                bmmsgs[0:nmsgs] = msgs[nsentmsgs : nsentmsgs + nmsgs]
            else:
                for i in range(nmsgs):
                    bmmsgs[i] = self._to_bm_can_message(msgs[i + nsentmsgs])
            nbmmsgs = ctypes.c_uint32(nmsgs)
            try:
                if self._handle is not None:
                    bmapi.BM_WriteMultipleCanMessage(
                        self._handle,
                        dataptr,
                        ctypes.byref(nbmmsgs),
                        nullptr,
                        timeoutms,
                        nullptr,
                    )
                else:
                    self.recover_from_error()
            except BmError as exc:
                self.recover_from_error()
                if self._raise_on_send_error:
                    raise BmOperationError.from_generic(exc) from exc

            nsentmsgs += nmsgs

    def cancel_send(self) -> None:
        try:
            bmapi.BM_CancelWrite(self._handle)
        except BmError as exc:
            raise BmOperationError.from_generic(exc) from exc

    def clear_buffer(self) -> None:
        with self._lock:
            try:
                bmapi.BM_ClearBuffer(self._handle)
            except BmError as exc:
                raise BmOperationError.from_generic(exc) from exc

    def open_channel(self):
        self._handle = bmapi.BM_ChannelHandle()
        try:
            bmapi.BM_OpenEx(
                ctypes.byref(self._handle),
                ctypes.byref(self._channelinfo),
                self._mode,
                self._tres,
                ctypes.byref(self._bitrate),
                ctypes.cast(
                    ctypes.c_void_p(), ctypes.POINTER(bmapi.BM_RxFilterListTypeDef)
                ),
                0,
            )
        except BmError as e:
            self._handle = None

    def shutdown(self) -> None:
        try:
            super().shutdown()
        except AttributeError:
            pass

        if self in self.bus_list:
            self.bus_list.remove(self)
        handle = getattr(self, "_handle", None)
        if handle:
            try:
                bmapi.BM_Close(handle)
            except BmError as exc:
                raise BmOperationError.from_generic(exc) from exc
        self._handle = bmapi.BM_ChannelHandle()

    def reset(self) -> None:
        try:
            bmapi.BM_Reset(self._handle)
        except BmError as exc:
            raise BmOperationError.from_generic(exc) from exc

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        mode_changed = False
        self._state = new_state
        if new_state is BusState.ACTIVE:
            if (
                self._mode == bmapi.BM_CAN_OFF_MODE
                or self._mode == bmapi.BM_CAN_LISTEN_ONLY_MODE
            ):
                self._mode = bmapi.BM_CAN_NORMAL_MODE
                mode_changed = True
            else:
                pass  # Do not change (i.e. loopback)
        elif new_state is BusState.PASSIVE:
            # When this mode is set, the CAN controller does not take part on active events (eg. transmit CAN messages)
            # but stays in a passive mode (CAN monitor), in which it can analyse the traffic on the CAN bus used by a BMCAN channel.
            if self._mode != bmapi.BM_CAN_LISTEN_ONLY_MODE:
                self._mode = bmapi.BM_CAN_LISTEN_ONLY_MODE
                mode_changed = True
        if mode_changed:
            bmapi.BM_SetCanMode(self._handle, self._mode)

    @classmethod
    def enumerate(cls):
        BmCanBus.__init_class__()
        infolist = bmapi.BM_ChannelInfoListTypeDef()
        numOfInfo = ctypes.c_int(len(infolist.entries))
        bmapi.BM_EnumerateByCap(
            ctypes.byref(infolist), ctypes.byref(numOfInfo), bmapi.BM_CAN_CAP
        )
        channellist = []
        for i in range(numOfInfo.value):
            channellist.append(
                {
                    "index": i,
                    "name": infolist.entries[i].name.decode(),
                    # Add other exports here
                }
            )
        return channellist

    def recover_from_error(self):
        # // Note: It takes some time to run BM_Close and BM_OpenEx,
        # // and channel handles will be invalidated when recovering from error,
        # // make sure no other threads are using channel handles (e.g. calling BM_Write) during the recovery,
        # // or, use single-threaded design, just like this demo.
        current_time = time.time()
        if (current_time - self._previous_recovery_ts) >= 1:
            canStatus = bmapi.BM_CanStatusInfoTypedef()
            canTes = bmapi.BM_TerminalResistorTypeDef()
            canBitrate = bmapi.BM_BitrateTypeDef()
            with self._lock:
                try:
                    status = bmapi.BM_GetStatus(self._handle, ctypes.byref(canStatus))
                    oldmode = bmapi.BM_CanModeTypeDef()
                    bmapi.BM_GetCanMode(self._handle, ctypes.byref(oldmode))
                    bmapi.BM_GetTerminalRegister(self._handle, ctypes.byref(canTes))
                    bmapi.BM_GetBitrate(self._handle, ctypes.byref(canBitrate))
                except Exception as e:
                    if self._debug:
                        self._log.error(
                            f"{self.channel_info} Error 1 when configuring can: {str(e)}"
                        )
            if self._debug:
                self._log.info(
                    "=== Channel: %s - [Before busoff recovery] ===\n"
                    "  TXBO: %d (Transmit Bus Off)    | TXBP: %d (Transmit Bus Passive)\n"
                    "  RXBP: %d (Receive Bus Passive)  | TXWARN: %d (Transmit Warning)\n"
                    "  RXWARN: %d (Receive Warning)  | TEC: %d (Transmit Error Counter)\n"
                    "  REC: %d (Receive Error Counter)",
                    self.channel_info,
                    canStatus.TXBO,
                    canStatus.TXBP,
                    canStatus.RXBP,
                    canStatus.TXWARN,
                    canStatus.RXWARN,
                    canStatus.TEC,
                    canStatus.REC,
                )
                self._log.info(
                    "=== Channel: %s - [Before busoff recovery] ===\n"
                    "  status = %d\n",
                    self.channel_info,
                    status,
                )
                self._log.info(
                    "=== Channel: %s - [Before busoff recovery - Config] ===\n"
                    "  CAN Mode: 0x%02X | Terminal Resistor: 0x%04X\n"
                    "  Nominal Bitrate: %d kbps | Data Bitrate: %d kbps\n"
                    "  Nominal Sample Pos: %d%% | Data Sample Pos: %d%%",
                    self.channel_info,
                    oldmode.value,
                    canTes.value,
                    canBitrate.nbitrate,
                    canBitrate.dbitrate,
                    canBitrate.nsamplepos,
                    canBitrate.dsamplepos,
                )
            if status & bmapi.BM_ERROR_INITIALIZE:
                # // BM_Init() is not called yet.
                # // Read our SDK documentation for details.
                print("BM_ERROR_INITIALIZE\n")
            elif status & bmapi.BM_ERROR_ILLPARAMVAL:
                # // Channel handle is invalid.
                # // Maybe it's not opened yet (using BM_OpenEx) or already closed?
                print("BM_ERROR_ILLPARAMVAL\n")
                self._previous_recovery_ts = current_time
                for i in range(len(self.bus_list)):
                    bus = self.bus_list[i]
                    if bus._handle == None:
                        if bytes(self._channelinfo.sn) == bytes(
                            bus._channelinfo.sn
                        ) and bytes(self._channelinfo.uid) == bytes(
                            bus._channelinfo.uid
                        ):
                            bus.open_channel()
            elif status & bmapi.BM_ERROR_ILLOPERATION:
                # // USB Device operation failed.
                # // Maybe the device is unplugged from host PC?
                print("BM_ERROR_ILLOPERATION\n")
                self._previous_recovery_ts = current_time
                # // Close all channels in the same device.
                try:
                    for i in range(len(self.bus_list)):
                        bus = self.bus_list[i]
                        if bus._handle != None:
                            if bytes(self._channelinfo.sn) == bytes(
                                bus._channelinfo.sn
                            ) and bytes(self._channelinfo.uid) == bytes(
                                bus._channelinfo.uid
                            ):
                                bmapi.BM_Close(bus._handle)
                                bus._handle = None
                    # // Try reset device and remove device from opened device list kept by bmapi.
                    for i in range(len(self.bus_list)):
                        bus = self.bus_list[i]
                    if bus._handle == None:
                        if bytes(self._channelinfo.sn) == bytes(
                            bus._channelinfo.sn
                        ) and bytes(self._channelinfo.uid) == bytes(
                            bus._channelinfo.uid
                        ):
                            bus.open_channel()
                except Exception as e:
                    if self._debug:
                        self._log.error(
                            f"{self.channel_info} Error 2 when configuring can: status & bmapi.BM_ERROR_ILLOPERATION: {str(e)}"
                        )
            elif status & bmapi.BM_ERROR_ANYBUSERR:
                # // BUSOFF
                # // Maybe the remote CAN device is disconnected,
                # // or you might want to check your bitrate, sample-position, tres configuration.
                if self._debug:
                    self._log.info("%sBUSOFF RECOVERY", self.channel_info)
                else:
                    print("BUSOFF RECOVERY\n")
                self._previous_recovery_ts = current_time
                with self._lock:
                    try:
                        bmapi.BM_RecoverBusOff(self._handle)
                    except Exception as e:
                        if self._debug:
                            self._log.error(
                                f"{self.channel_info} Error when recovering from busoff: {str(e)}"
                            )

    def send_isotp(self, payload, timeout=-1):
        timeout_ms = int(timeout * 1000.0) if timeout >= 0 else -1
        try:
            if self._handle != None:
                bmapi.BM_WriteIsotp(
                    self._handle,
                    ctypes.c_char_p(payload),
                    len(payload),
                    timeout_ms,
                    ctypes.byref(self._isotp_config),
                )
            else:
                self.recover_from_error()
        except BmError as e:
            self.recover_from_error()

    def receive_isotp(self, timeout=-1, max_len=4095):
        timeout_ms = int(timeout * 1000.0) if timeout >= 0 else -1
        buf = ctypes.create_string_buffer(max_len)
        received_len = ctypes.c_uint32(len(buf))
        if self._handle != None:
            bmapi.BM_ReadIsotp(
                self._handle,
                buf,
                ctypes.byref(received_len),
                timeout_ms,
                ctypes.byref(self._isotp_config),
            )
        else:
            self.recover_from_error()
        return bytes(buf[: received_len.value])

    def config_isotp(
        self, tester_msg_id, ecu_msg_id, mode=bmapi.BM_ISOTP_NORMAL_TESTER, **kwargs
    ):
        self._isotp_config.mode = mode
        enable_fdf = kwargs.get("fd", False) or kwargs.get("fdf", False)
        enable_brs = kwargs.get("brs", enable_fdf)
        enable_ide = (
            kwargs.get("ide", False) or tester_msg_id > 0x7FF or ecu_msg_id > 0x7FF
        )
        dlc = kwargs.get("dlc", 0xF if enable_fdf else 0x8)
        testerMsg = self._isotp_config.testerDataTemplate.getCanMessage()
        testerMsg.ctrl.tx.FDF = enable_fdf
        testerMsg.ctrl.tx.BRS = enable_brs
        testerMsg.ctrl.tx.IDE = enable_ide
        testerMsg.ctrl.tx.DLC = dlc
        testerMsg.setMessageId(tester_msg_id)
        self._isotp_config.testerDataTemplate.setCanMessage(testerMsg)
        ecuMsg = self._isotp_config.ecuDataTemplate.getCanMessage()
        ecuMsg.ctrl.tx.FDF = enable_fdf
        ecuMsg.ctrl.tx.BRS = enable_brs
        ecuMsg.ctrl.tx.IDE = enable_ide
        ecuMsg.ctrl.tx.DLC = dlc
        ecuMsg.setMessageId(ecu_msg_id)
        self._isotp_config.ecuDataTemplate.setCanMessage(ecuMsg)
        padding = kwargs.get("padding", None)
        if padding is not None:
            self._isotp_config.paddingEnabled = 1
            self._isotp_config.paddingValue = ctypes.c_uint8(padding)
        else:
            self._isotp_config.paddingEnabled = 0
        # Note if dlc > 8, BMAPI would ignore 'longPduEnabled' and always enable longPdu
        self._isotp_config.longPduEnabled = kwargs.get("longpdu", False)
        self._isotp_config.functionalAddressingEnabled = kwargs.get("functional", False)
        disable_hardware_isotp = kwargs.get("disable_hardware_isotp", None)
        if disable_hardware_isotp is None and "hardware_isotp" in kwargs:
            disable_hardware_isotp = not kwargs["hardware_isotp"]
        if disable_hardware_isotp is None:
            disable_hardware_isotp = False
        if hasattr(self._isotp_config.flowcontrol, "hardwareIsotpDisabled"):
            self._isotp_config.flowcontrol.hardwareIsotpDisabled = int(
                disable_hardware_isotp
            )
        else:
            self._isotp_config.flowcontrol.reserved = int(disable_hardware_isotp)
        ecuTimeout = kwargs.get("ecuTimeout", {})
        testerTimeout = kwargs.get("testerTimeout", {})
        self._isotp_config.ecuTimeout.a = ecuTimeout.get("a", 0)
        self._isotp_config.ecuTimeout.b = ecuTimeout.get("b", 0)
        self._isotp_config.ecuTimeout.c = ecuTimeout.get("c", 0)
        self._isotp_config.testerTimeout.a = testerTimeout.get("a", 0)
        # Test would wait for FC from ECU until 'testerTimeout.b' timeout occurs.
        self._isotp_config.testerTimeout.b = testerTimeout.get("b", 50)
        self._isotp_config.testerTimeout.c = testerTimeout.get("c", 0)
