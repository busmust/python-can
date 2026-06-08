"""Send one CAN FD frame through a BUSMUST BMAPI channel."""

import argparse

import can


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=0, type=int)
    parser.add_argument("--ack-channel", default=None, type=int)
    parser.add_argument("--bitrate", default=500000, type=int)
    parser.add_argument("--data-bitrate", default=2000000, type=int)
    parser.add_argument("--timeout", default=1.0, type=float)
    args = parser.parse_args()

    ack_bus = None
    with can.Bus(
        interface="bmcan",
        channel=args.channel,
        fd=True,
        bitrate=args.bitrate,
        data_bitrate=args.data_bitrate,
        tres=True,
    ) as bus:
        try:
            if args.ack_channel is not None:
                ack_bus = can.Bus(
                    interface="bmcan",
                    channel=args.ack_channel,
                    fd=True,
                    bitrate=args.bitrate,
                    data_bitrate=args.data_bitrate,
                    tres=True,
                )
            msg = can.Message(
                arbitration_id=0x123,
                data=bytes(range(64)),
                is_fd=True,
                bitrate_switch=True,
                is_extended_id=False,
            )
            bus.send(msg, timeout=args.timeout)
            print(f"Sent: {msg}")
        finally:
            if ack_bus is not None:
                ack_bus.shutdown()


if __name__ == "__main__":
    main()
