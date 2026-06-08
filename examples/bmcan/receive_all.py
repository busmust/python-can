"""Receive and print frames from a BUSMUST BMAPI channel."""

import argparse
import time

import can


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=0, type=int)
    parser.add_argument("--bitrate", default=500000, type=int)
    parser.add_argument("--data-bitrate", default=2000000, type=int)
    parser.add_argument("--duration", default=None, type=float)
    parser.add_argument("--count", default=None, type=int)
    args = parser.parse_args()

    with can.Bus(
        interface="bmcan",
        channel=args.channel,
        fd=True,
        bitrate=args.bitrate,
        data_bitrate=args.data_bitrate,
        tres=True,
    ) as bus:
        received = 0
        end_time = time.time() + args.duration if args.duration is not None else None
        while end_time is None or time.time() < end_time:
            msg = bus.recv(timeout=1.0)
            if msg is not None:
                print(msg)
                received += 1
                if args.count is not None and received >= args.count:
                    break


if __name__ == "__main__":
    main()
