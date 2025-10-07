import argparse
from hikrobot_camera import band_width


#  uv run --with windows-curses python -m test.bandwidth


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A command for monitoring the traffic of network interface! Ctrl + C: exit"
    )
    parser.add_argument(
        "-t", "--time",
        type=int,
        help="the interval time for ouput",
        default=1
    )
    parser.add_argument(
        "-u",
        "--unit",
        type=str,
        choices=["b", "B", "k", "K", "m", "M", "g", "G"],
        help="the unit for ouput",
        default="M",
    )
    parser.add_argument(
        "-v",
        "--version",
        help="output version information and exit",
        action="store_true",
    )
    args = parser.parse_args()
    if args.version:
        print(band_width.version())
        exit(0)

    num = args.time
    unit = args.unit
    band_width.output(num, unit)