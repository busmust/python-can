BUSMUST BMAPI
=============

This interface adds support for BUSMUST USB CAN FD adapters through the BMAPI
driver library.

Configuration
-------------

Example configuration for the first available BUSMUST CAN channel:

::

    [default]
    interface = bmcan
    channel = 0
    fd = true
    bitrate = 500000
    data_bitrate = 2000000
    tres = true

``channel``
 The channel index returned by BMAPI enumeration, starting at ``0``. A channel
 name string such as ``"BM-CANFD-X4-PRO(0000) CH1"`` may also be used.

``fd`` (default ``True``)
 Open the channel in CAN FD mode. Set to ``False`` for classic CAN.

``bitrate`` (default ``500000``)
 Nominal/arbitration bitrate in bit/s.

``data_bitrate`` (default ``500000``)
 CAN FD data phase bitrate in bit/s.

``samplepos`` and ``data_samplepos`` (default ``75``)
 Nominal and data phase sample point in percent.

``tres`` (default ``False``)
 Enable the 120 Ohm terminal resistor.

``receive_own_messages`` (default ``False``)
 Open the channel in external loopback mode.

``listen_only`` (default ``False``)
 Open the channel in listen-only mode.

``remote_ip``
 Optional IPv4 address for BMAPI remote enumeration, for example
 ``"192.168.41.255"``.

``raise_on_send_error`` (default ``False``)
 By default, BMAPI send errors trigger :meth:`~can.interfaces.bmcan.BmCanBus.recover_from_error`
 and are then absorbed for compatibility with the BUSMUST BMAPI SDK python-can
 4.0.0 backend. Set this option to ``True`` to raise
 :class:`~can.interfaces.bmcan.BmOperationError` after the recovery check, which
 is closer to the generic python-can :meth:`~can.BusABC.send` contract.

Driver library loading
----------------------

The BMAPI dynamic library is not distributed with python-can. Install the BMAPI
SDK or make the matching library available to the operating system loader.

On Windows, make ``BMAPI.dll`` or ``BMAPI64.dll`` available through ``PATH``.
On Linux, make ``libbmapi.so`` or ``libbmapi64.so`` available through the
system library path, ``LD_LIBRARY_PATH``, or another loader configuration
mechanism.

The backend also checks the package directory and PyInstaller bundle directory
for the matching BMAPI library name. This is intended for local applications
that package BMAPI themselves; the python-can source tree should not contain
BMAPI binaries.

Periodic transmission
---------------------

``BmCanBus`` uses BMAPI hardware transmit tasks for single-message periodic
transmission when the device reports available TX task slots. Calls that use
multiple messages, ``modifier_callback``, ``autostart=False``, or devices
without hardware TX task support fall back to python-can's thread-based cyclic
sender.

Stopping the object returned by :meth:`~can.BusABC.send_periodic`, or calling
:meth:`~can.BusABC.stop_all_periodic_tasks`, stops the BMAPI hardware task.
The BMAPI-specific :meth:`~can.interfaces.bmcan.BmCanBus.cancel_send` method
can be used to cancel pending blocking writes on the channel.

Bus
---

.. autoclass:: can.interfaces.bmcan.BmCanBus
   :show-inheritance:
   :member-order: bysource
   :members:
      enumerate,
      send,
      send_multiple,
      send_periodic,
      stop_all_periodic_tasks,
      cancel_send,
      clear_buffer,
      reset,
      shutdown,
      recover_from_error,
      config_isotp,
      send_isotp,
      receive_isotp

Exceptions
----------

.. autoexception:: can.interfaces.bmcan.BmError
   :show-inheritance:
.. autoexception:: can.interfaces.bmcan.BmInitializationError
   :show-inheritance:
.. autoexception:: can.interfaces.bmcan.BmOperationError
   :show-inheritance:
