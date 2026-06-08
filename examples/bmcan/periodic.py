"""Send a periodic frame through a BUSMUST BMAPI channel."""

import argparse
import time

import can


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=0, type=int)
    parser.add_argument("--ack-channel", default=None, type=int)
    parser.add_argument("--bitrate", default=500000, type=int)
    parser.add_argument("--period", default=0.1, type=float)
    parser.add_argument("--duration", default=5.0, type=float)
    args = parser.parse_args()

    ack_bus = None
    with can.Bus(
        interface="bmcan",
        channel=args.channel,
        fd=False,
        bitrate=args.bitrate,
        tres=True,
    ) as bus:
        try:
            if args.ack_channel is not None:
                ack_bus = can.Bus(
                    interface="bmcan",
                    channel=args.ack_channel,
                    fd=False,
                    bitrate=args.bitrate,
                    tres=True,
                )
            msg = can.Message(arbitration_id=0x123, data=[1, 2, 3, 4])
            task = bus.send_periodic(msg, period=args.period)
            time.sleep(args.duration)
            task.stop()
            print("Stopped periodic transmit task")
        finally:
            if ack_bus is not None:
                ack_bus.shutdown()


if __name__ == "__main__":
    main()
