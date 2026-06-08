"""Connect to a BUSMUST BMAPI local or remote device."""

import argparse

import can


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-ip", default=None)
    parser.add_argument("--channel", default=0, type=int)
    parser.add_argument("--bitrate", default=500000, type=int)
    parser.add_argument("--data-bitrate", default=2000000, type=int)
    args = parser.parse_args()

    with can.Bus(
        interface="bmcan",
        remote_ip=args.remote_ip,
        channel=args.channel,
        fd=True,
        bitrate=args.bitrate,
        data_bitrate=args.data_bitrate,
        tres=True,
    ) as bus:
        print(f"Opened {bus.channel_info}")


if __name__ == "__main__":
    main()
