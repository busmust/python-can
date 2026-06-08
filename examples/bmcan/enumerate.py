"""List BUSMUST BMAPI CAN channels."""

from can.interfaces.bmcan import BmCanBus


def main() -> None:
    for channel in BmCanBus.enumerate():
        print(f"{channel['index']}: {channel['name']}")


if __name__ == "__main__":
    main()
