"""Some utils."""
import argparse
import logging
import os
import random
import re
import statistics
import string
import sys
import typing
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from time import sleep
from typing import (
    Callable,
    Iterable,
    Iterator,
    NamedTuple,
    Optional,
    Sequence,
    TypeVar,
    Union,
)

import humanize
import numpy as np
import requests
import seaborn as sns
import termcolor
from dateutil.parser import parse as parse_date
from matplotlib import pyplot as plt
from prompt_toolkit import print_formatted_text
from prompt_toolkit.shortcuts import ProgressBar
from termcolor import colored, cprint
from urllib3.util.url import Url, parse_url
from yaspin import yaspin

from exceptions import ConflictError

if typing.TYPE_CHECKING:
    from collections.abc import Iterable


#: monkey patch termcolor:
termcolor.ATTRIBUTES["italic"] = 3

T = TypeVar("T")


def random_string(length: int):
    """Generate a random string of fixed length"""
    letters = string.ascii_lowercase

    return "".join(random.choice(letters) for i in range(length))


def compare_and_merge(property: str, obj1, obj2, error_msg: str):
    val1 = getattr(obj1, property, None)
    val2 = getattr(obj2, property, None)

    if not val1 and not val2:
        if val1 != val2:
            raise ConflictError(
                f"{error_msg}: {property.lstrip('_')}: {val1} != {val2}"
            )
        else:
            return val1
    elif not val1:
        return val2
    else:
        return val1


class Statistics(NamedTuple):
    """Statistics for a dataset."""

    #: The average
    avg: float
    #: The median
    med: Union[int, float]
    #: The variance
    var: float
    #: The standard deviation
    std: float
    #: The amount of values
    num: int
    #: The sum of all values
    sum: Union[int, float]
    #: The maximum
    max: Union[int, float]
    #: The minimum
    min: Union[int, float]
    #: The 0.95 quantile
    q_95: float
    #: The 0.05 quantile
    q_05: float

    def mpl_label_short(
        self,
        formatter: Callable[[Union[float, int]], str] = str,
    ) -> str:
        """:Return: A short label for matplotlib."""
        trans = str.maketrans(
            {
                " ": r"\,",
                "%": r"\%",
            }
        )
        avg_str = formatter(self.avg).translate(trans)
        # med_str = formatter(self.med).translate(trans)
        std_str = formatter(self.std).translate(trans)
        min_str = formatter(self.min).translate(trans)
        max_str = formatter(self.max).translate(trans)

        return "\n".join(
            (
                fr"$\mu = {avg_str}\,\left(\pm {std_str}\right)$",
                fr"Range: ${min_str}..{max_str}$",
            )
        )

    def mpl_label(
        self,
        formatter: Callable[[Union[float, int]], str] = str,
    ) -> str:
        """:Return: A short label for matplotlib."""
        trans = str.maketrans(
            {
                " ": r"\,",
                "%": r"\%",
            }
        )
        avg_str = formatter(self.avg).translate(trans)
        med_str = formatter(self.med).translate(trans)
        std_str = formatter(self.std).translate(trans)
        min_str = formatter(self.min).translate(trans)
        max_str = formatter(self.max).translate(trans)

        return "\n".join(
            (
                fr"$\mu = {avg_str}$",
                fr"$\mathrm{{median}} = {med_str}$",
                fr"$\sigma = {std_str}$",
                fr"Range: ${min_str}..{max_str}$",
            )
        )

    def mpl_label_narrow(
        self,
        formatter: Callable[[Union[float, int]], str] = str,
    ) -> str:
        """:Return: A narrow label for matplotlib."""
        trans = str.maketrans(
            {
                " ": r"\,",
                "%": r"\%",
            }
        )
        avg_str = formatter(self.avg).translate(trans)
        med_str = formatter(self.med).translate(trans)
        std_str = formatter(self.std).translate(trans)
        min_str = formatter(self.min).translate(trans)
        max_str = formatter(self.max).translate(trans)

        return "\n".join(
            (
                fr"$\mu = {avg_str}$",
                fr"$\mathrm{{Md}} = {med_str}$",
                fr"$\sigma = {std_str}$",
                fr"$\min = {min_str}$",
                fr"$\max = {max_str}$",
            )
        )

    @classmethod
    def calc(cls, data: Union[Sequence[int], Sequence[float]]) -> "Statistics":
        """Calculate statistics for data."""
        avg = statistics.fmean(data)
        try:
            var = statistics.variance(data, avg)
            std = statistics.stdev(data)
        except statistics.StatisticsError as err:
            if "variance requires at least two data points" in str(err).lower():
                var = 0.0
                std = 0.0
            else:
                raise err

        return cls(
            avg=avg,
            med=statistics.median(data),
            var=var,
            std=std,
            sum=statistics.fsum(data),  # type: ignore
            num=len(data),
            max=max(data),
            min=min(data),
            q_95=np.quantile(data, q=0.95),  # type: ignore
            q_05=np.quantile(data, q=0.05),  # type: ignore
        )


def map2d(func: Callable[["Iterable[T]"], T], arrays: "Iterable[Iterable[T]]") -> T:
    """Map func to arrays and to each entry of arrays."""

    return func(map(func, arrays))


def map3d(
    func: Callable[["Iterable[T]"], T],
    arrays: "Iterable[Iterable[Iterable[T]]]",
) -> T:
    def inner_func(arr):
        return map2d(func, arr)

    return func(map(inner_func, arrays))


class YaspinWrapper:
    def __init__(self, debug: bool, text: str, color: str):
        self.debug = debug
        self._text = text
        self.color = color
        self._result_set = False

        if not debug:
            self.yaspin = yaspin(text=text, color=color)

    def __enter__(self):
        if self.debug:
            self._update()
        else:
            self.yaspin.__enter__()

        return self

    def __exit__(self, err, args, traceback):
        if not self._result_set:
            if err:
                self.fail("⨯")
            else:
                self.ok("✔")

        if not self.debug:
            self.yaspin.__exit__(err, args, traceback)

    @property
    def text(self) -> str:
        if self.debug:
            return self._text
        else:
            return self.yaspin.text

    @text.setter
    def text(self, value: str):
        if self.debug:
            self._text = value
            self._update()
        else:
            self.yaspin.text = value

    def hidden(self):
        if self.debug:
            return self
        else:
            return self.yaspin.hidden()

    def hide(self):
        if not self.debug:
            return self.yaspin.hide()

    def show(self):
        if not self.debug:
            return self.yaspin.show()

    def _update(self):
        if self.debug:
            cprint(f"⚒ {self.text}", color=self.color, end="\r", flush=True)

    def ok(self, text: str):
        self._result_set = True

        if self.debug:
            print(text)
        else:
            self.yaspin.ok(text)

    def fail(self, text: str):
        self._result_set = True

        if self.debug:
            print(text, file=sys.stderr)
        else:
            self.yaspin.fail(text)

    def write(self, text: str):
        if self.debug:
            print(text)
            self._update()
        else:
            self.yaspin.write(text)


class ProgBarWrapper:
    def __init__(self, hide: bool, *args, **kwargs):
        self.hide = hide
        if not hide:
            self.pb = ProgressBar(*args, **kwargs)
        else:
            self.pb = None

    def __enter__(self):
        if self.pb:
            return self.pb.__enter__()
        else:
            return self

    def __exit__(self, err, args, traceback):
        if self.pb:
            return self.pb.__exit__(err, args, traceback)

    def __call__(self, iter: Iterator, label, total, *args, **kwargs) -> Iterator:
        print_formatted_text(label, f"({total})")
        return iter


class HideCursor:
    def __enter__(self, *args, **kwargs):
        """hide cursor"""
        print("\x1b[?25l")

    def __exit__(self, *args, **kwargs):
        """show cursor"""
        print("\x1b[?25h")


def clear_line(**kwargs):
    """Clear current line."""
    print("\033[1K", end="\r", **kwargs)


def create_relpath(path1: Path, path2: Optional[Path] = None) -> Path:
    """Create a relative path for path1 relative to path2. TODO this is broken."""

    if not path2:
        path2 = Path(".")

    path1 = path1.absolute()
    path2 = path2.absolute()

    common_prefix = Path(os.path.commonprefix((path1, path2)))

    return Path(os.path.relpath(path1, common_prefix))


def existing_dir_path(value: str, allow_none=False) -> Optional[Path]:
    if not value or value.lower() == "none":
        if allow_none:
            return None
        else:
            raise argparse.ArgumentTypeError("`none` is not allowed here.")

    path = Path(value)

    if path.is_file():
        raise argparse.ArgumentTypeError(f"{value} is a file. A directory is required.")

    elif not path.is_dir():
        raise argparse.ArgumentTypeError(f"{value} does not exist.")

    return path


def existing_file_path(value: str, allow_none=False) -> Optional[Path]:
    if not value or value.lower() == "none":
        if allow_none:
            return None
        else:
            raise argparse.ArgumentTypeError("`none` is not allowed here.")

    path = Path(value)

    if path.is_dir():
        raise argparse.ArgumentTypeError(f"{value} is a directory. A file is required.")

    elif not path.is_file():
        raise argparse.ArgumentTypeError(f"{value} does not exist.")

    return path


def time_range(value: str) -> tuple[time, time]:
    """Parse two time values in the format HH:MM separated by `-`."""
    values = value.strip().split("-")

    if len(values) != 2:
        raise argparse.ArgumentTypeError(
            f"{value} is not a valid time range. It must be separated by -"
        )

    start_str, end_str = values
    start_bits = start_str.split(":")

    if len(start_bits) != 2:
        raise argparse.ArgumentTypeError(
            f"{start_str} is not a valid time. It must be in HH:MM format."
        )

    start_hour_str, start_minute_str = start_bits
    try:
        start_hour = int(start_hour_str)
        start_minute = int(start_minute_str)
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            f"{start_str} is not a valid time. Hour and minutes must be integer."
        ) from err

    end_bits = end_str.split(":")

    if len(end_bits) != 2:
        raise argparse.ArgumentTypeError(
            f"{end_str} is not a valid time. It must be in HH:MM format."
        )

    end_hour_str, end_minute_str = end_bits
    try:
        end_hour = int(end_hour_str)
        end_minute = int(end_minute_str)
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            f"{end_str} is not a valid time. Hour and minutes must be integer."
        ) from err

    start = time(hour=start_hour, minute=start_minute)
    end = time(hour=end_hour, minute=end_minute)

    return start, end


ARGPARSE_BOOLEAN_CHOICES = {
    "y": True,
    "yes": True,
    "t": True,
    "true": True,
    "1": True,
    "n": False,
    "no": False,
    "f": False,
    "false": False,
    "0": False,
}


def argparse_boolean_type(value: str):
    value = value.lower()
    if value not in ARGPARSE_BOOLEAN_CHOICES.keys():
        valid_values_str = ", ".join(sorted(ARGPARSE_BOOLEAN_CHOICES.keys()))
        raise argparse.ArgumentTypeError(
            f"Invalid value {value}. Valid boolean values are {valid_values_str}"
        )

    return ARGPARSE_BOOLEAN_CHOICES[value]


def time_total_seconds(value: time) -> int:
    """Return the total number of seconds in a time object."""

    return value.hour * 3600 + value.minute * 60 + value.second


def sleep_between(start: time, end: time) -> None:
    """Sleep if the current time is between start and end."""

    while True:
        now = datetime.now()

        if start <= now.time() <= end:
            until_seconds = time_total_seconds(end)
            now_total_seconds = time_total_seconds(now.time())
            pause_for = until_seconds - now_total_seconds
            pause_until = now + timedelta(seconds=pause_for)
            LOGGER.info(
                "=== Pausing until %s (for %s sec) ===",
                pause_until.strftime("%Y-%m-%d %H:%M"),
                pause_for,
            )
            sleep(pause_for)
        else:
            break


class UrlOrPath:
    def __init__(self, src: Union[str, Path, Url, "UrlOrPath"]):
        if isinstance(src, UrlOrPath):
            self.src: Union[Url, Path] = src.src
        elif isinstance(src, Url):
            self.src = src
        elif isinstance(src, Path):
            self.src = src
        else:
            url = parse_url(src)

            if not url.host or not url.scheme:
                self.src = Path(src)
            else:
                self.src = url

    def __str__(self):
        return str(self.src)

    def __repr__(self):
        return f"{self.__class__.__name__}({repr(self.src)})"

    @property
    def is_path(self):
        return isinstance(self.src, Path)

    def read(self, mode="r"):
        if isinstance(self.src, Path):
            with self.path.open(mode) as file:
                return file.read()
        else:
            resp = requests.get(self.src)
            resp.raise_for_status()

            return resp.text

    def readline(self):
        if isinstance(self.src, Path):
            with self.path.open("r") as file:
                yield from file.readlines()
        else:
            # breakpoint()
            response = requests.get(self.src, stream=True)
            response.raise_for_status()
            buf = ""
            for chunk in response.iter_content(chunk_size=8192):
                buf += chunk.decode("utf-8")
                lines = buf.splitlines()
                buf = lines[-1]
                yield from lines[:-1]

            yield buf

    @property
    def scheme(self):
        return self.src.scheme if isinstance(self.src, Url) else None

    @property
    def auth(self):
        return self.src.auth if isinstance(self.src, Url) else None

    @property
    def host(self):
        return self.src.host if isinstance(self.src, Url) else None

    @property
    def port(self):
        return self.src.port if isinstance(self.src, Url) else None

    @property
    def path(self) -> Path:
        return Path(self.src.path) if isinstance(self.src, Url) else self.src

    @property
    def url(self) -> Url:
        return Url(
            scheme=self.scheme,
            auth=self.auth,
            host=self.host,
            port=self.port,
            path=str(self.path),
        )

    @property
    def parent(self) -> "UrlOrPath":
        if isinstance(self.src, Path):
            return UrlOrPath(self.path.parent)
        else:
            return UrlOrPath(
                Url(
                    scheme=self.scheme,
                    auth=self.auth,
                    host=self.host,
                    port=self.port,
                    path=str(self.path.parent),
                )
            )

    def __truediv__(self, other: Union[str, Path, "UrlOrPath"]) -> "UrlOrPath":
        if isinstance(other, UrlOrPath):
            return UrlOrPath(self.path / other.path)
        elif isinstance(self.src, Path):
            return UrlOrPath(self.path / other)
        else:
            return UrlOrPath(
                Url(
                    scheme=self.scheme,
                    auth=self.auth,
                    host=self.host,
                    port=self.port,
                    path=str(self.path / other),
                )
            )

    def __rtruediv__(self, other: Union[str, Path, "UrlOrPath"]) -> "UrlOrPath":
        if isinstance(other, UrlOrPath):
            return UrlOrPath(self.path / other.path)
        elif isinstance(self.src, Path):
            return UrlOrPath(Path(other) / self.path)
        else:
            return UrlOrPath(
                Url(
                    scheme=self.scheme,
                    auth=self.auth,
                    host=self.host,
                    port=self.port,
                    path=str(other / self.path),
                )
            )

    def is_dir(self) -> bool:
        return self.path.is_dir()

    def is_absolute(self) -> bool:
        return self.path.is_absolute()

    def is_file(self) -> bool:
        if isinstance(self.src, Path):
            return self.path.is_file()
        else:
            resp = requests.head(self.src)

            if resp.status_code == 404:
                return False

            resp.raise_for_status()

            return True

    @property
    def name(self):
        return self.path.name

    @property
    def mtime(self) -> datetime:
        """The modification date"""

        if isinstance(self.src, Path):
            return datetime.fromtimestamp(self.path.stat().st_mtime)
        else:
            resp = requests.head(self.src)
            resp.raise_for_status()

            return parse_date(resp.headers["Last-Modified"])

    @mtime.setter
    def mtime(self, value: datetime):
        self._mtime = value


@dataclass
class TraceTriple:
    left_pcap_path: Path
    right_pcap_path: Path
    keylog_path: Optional[Path] = None

    @classmethod
    def from_str(cls, value: str) -> "TraceTriple":
        parts = value.split(":")

        if len(parts) not in (2, 3):
            raise argparse.ArgumentTypeError(
                f"{value} is not a valid triple or tuple of paths separated by :"
            )

        path0 = Path(parts[0])
        path1 = Path(parts[1])
        path2 = Path(parts[2]) if len(parts) == 3 else None

        for path in (path0, path1, path2):
            if path:
                if path.is_dir():
                    raise argparse.ArgumentTypeError(
                        f"{path} is a directory. A file is required."
                    )
                elif not path.is_file():
                    raise argparse.ArgumentTypeError(f"{path} does not exist.")

        return cls(left_pcap_path=path0, right_pcap_path=path1, keylog_path=path2)


class Subplot:
    fig: plt.Figure
    ax: Union[plt.Axes, Sequence[plt.Axes], Sequence[Sequence[plt.Axes]]]

    def __init__(self, *args, **kwargs):
        self.fig, self.ax = plt.subplots(*args, **kwargs)
        sns.set()

    def __enter__(self):
        return self.fig, self.ax

    def __exit__(self, *args, **kwargs):
        plt.close(fig=self.fig)


def natural_data_rate(value: Union[int, float], short: bool = False) -> str:
    """Convert a value in bps to a natural string."""
    if short:
        replace_units = {
            "Byte": "",
            "Bytes": "",
            "kB": "k",
            "MB": "M",
            "GB": "G",
            "TB": "T",
        }
    else:
        replace_units = {
            "Byte": "bit/s",
            "Bytes": "bit/s",
            "kB": "kbit/s",
            "MB": "Mbit/s",
            "GB": "Gbit/s",
            "TB": "Tbit/s",
        }
    natural_file_size = humanize.naturalsize(value, binary=False)
    natural_value, file_size_unit = natural_file_size.split(" ", 1)
    replaced_unit = replace_units[file_size_unit]

    return f"{natural_value} {replaced_unit}"


class TerminalFormatter(logging.Formatter):
    """logging formatter with colors."""

    colors = {
        logging.DEBUG: "grey",
        logging.INFO: "cyan",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "red",
    }
    attrs = {
        logging.CRITICAL: ["bold"],
    }

    def __init__(self, fmt="%(asctime)s | %(message)s"):
        super().__init__(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record):
        return colored(
            super().format(record),
            color=self.colors[record.levelno],
            attrs=self.attrs.get(record.levelno),
        )


class LogFileFormatter(logging.Formatter):
    def format(self, record):
        msg = super(LogFileFormatter, self).format(record)
        # remove color control characters

        return re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]").sub("", msg)


LOGGER = logging.getLogger(name="quic-interop-runner")
LOGGER.setLevel(logging.DEBUG)
CONSOLE_LOG_HANDLER = logging.StreamHandler(stream=sys.stderr)
CONSOLE_LOG_HANDLER.setLevel(logging.DEBUG)
CONSOLE_LOG_HANDLER.setFormatter(TerminalFormatter())
LOGGER.addHandler(CONSOLE_LOG_HANDLER)
