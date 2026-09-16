"""Update the payload of a running BUSMUST hardware periodic task.

The hardware TX task keeps transmitting while ``modify_data()`` replaces the
frame payload, so the message period is not reset between updates (BMAPI
``BM_SetTxTask`` + ``BM_TXTASK_FLAGS_KEEP_CONTEXT``). This is the pattern for
alive counters, signal value refreshes, or E2E-protected data updated from the
host at a slower rate than the bus cycle.

Run with an optional second channel as the receiver:

    python -m examples.bmcan.periodic_modify --channel 0 --ack-channel 2
"""

import argparse
import time

import can


def channel_arg(value: str) -> int | str:
    """Accept either an enumeration index or a full channel name."""
    return int(value) if value.lstrip("-").isdigit() else value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=0, type=channel_arg)
    parser.add_argument("--ack-channel", default=None, type=channel_arg)
    parser.add_argument("--bitrate", default=500000, type=int)
    parser.add_argument("--period", default=0.05, type=float)
    parser.add_argument("--duration", default=10.0, type=float)
    parser.add_argument("--update-every", default=0.5, type=float)
    parser.add_argument(
        "--fd", action="store_true", help="use CAN FD with 2 Mbit/s data phase"
    )
    parser.add_argument("--data-bitrate", default=2000000, type=int)
    args = parser.parse_args()

    ack_bus = None
    with can.Bus(
        interface="bmcan",
        channel=args.channel,
        fd=args.fd,
        bitrate=args.bitrate,
        data_bitrate=args.data_bitrate,
        tres=True,
    ) as bus:
        try:
            if args.ack_channel is not None:
                ack_bus = can.Bus(
                    interface="bmcan",
                    channel=args.ack_channel,
                    fd=args.fd,
                    bitrate=args.bitrate,
                    data_bitrate=args.data_bitrate,
                    tres=True,
                )
            msg = can.Message(arbitration_id=0x123, data=[0, 0, 0, 0, 0, 0, 0, 0])
            task = bus.send_periodic(msg, period=args.period)
            print(
                f"Started periodic task, {1 / args.period:.0f} Hz; "
                f"updating payload every {args.update_every} s"
            )
            alive = 0
            deadline = time.monotonic() + args.duration
            next_update = time.monotonic()
            while time.monotonic() < deadline:
                if time.monotonic() >= next_update:
                    alive = (alive + 1) & 0xFF
                    # Same arbitration ID, new payload: the task keeps its
                    # schedule; the updated frame applies from the next cycle.
                    task.modify_data(
                        can.Message(
                            arbitration_id=0x123,
                            data=[alive, 0, 0, 0, 0, 0, 0, 0],
                        )
                    )
                    next_update += args.update_every
                if ack_bus is not None:
                    received = ack_bus.recv(timeout=0.1)
                    if received is not None:
                        print(
                            f"RX {received.arbitration_id:#05x} "
                            f"alive={received.data[0]} ts={received.timestamp:.6f}"
                        )
                else:
                    time.sleep(0.01)
            task.stop()
            print("Stopped periodic transmit task")
        finally:
            if ack_bus is not None:
                ack_bus.shutdown()


if __name__ == "__main__":
    main()
