#!/usr/bin/env python3

"""Plot time packet-number plots and more."""

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence, Union

import numpy as np
import prettytable
from humanize.filesize import naturalsize
from matplotlib import pyplot as plt
from termcolor import colored, cprint

from enums import CacheMode, PlotMode, Side
from tango_colors import Tango
from trace_analyzer2 import ParsingError, Trace
from utils import (
    Statistics,
    Subplot,
    TraceTriple,
    YaspinWrapper,
    create_relpath,
    natural_data_rate,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pyshark.packet.packet import Packet


DEFAULT_TITLES = {
    PlotMode.OFFSET_NUMBER: "Time vs. Offset-Number",
    PlotMode.PACKET_NUMBER: "Time vs. Packet-Number",
    PlotMode.FILE_SIZE: "Time vs. Transmitted File Size",
    PlotMode.PACKET_SIZE: "Time vs. Packet Size",
    PlotMode.DATA_RATE: "Time vs. Data Rate",
    PlotMode.RETURN_PATH: "Time vs. Return Path Data Rate",
    #  PlotMode.SIZE_HIST: "Size Histogram",
    #  PlotMode.RTT: "Time vs. RTT",
}


def parse_args():
    """Parse command line args."""
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "trace_triples",
        metavar="trace_triple",
        action="store",
        nargs="+",
        type=TraceTriple.from_str,
        help="':'-separated triples or tuples of the left pcap(ng) traces, right pcap(ng) traces and optional a keylog file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_file",
        action="store",
        type=Path,
        help="The output file.",
    )
    parser.add_argument(
        "-t",
        "--title",
        action="store",
        default=None,
        type=str,
        help="The title for the diagram.",
    )
    parser.add_argument(
        "--no-annotation",
        action="store_true",
        help="Hide TTFB, PLT, ... markers.",
    )
    parser.add_argument(
        "--mode",
        action="store",
        choices=PlotMode,
        type=PlotMode,
        default=PlotMode.OFFSET_NUMBER,
        help="The mode of plotting (time vs. packet-number or time vs. file-size",
    )
    parser.add_argument(
        "--cache",
        action="store",
        choices=CacheMode,
        type=CacheMode,
        default=CacheMode.LOAD,
        help="Cache parsed trace (store: create caches, load: load existing caches, both: load and store caches)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode.",
    )

    args = parser.parse_args()

    return args


@dataclass
class TraceAnalyzeResult:
    """The result of analyzing for one trace pair."""

    __FORMAT_VERSION__ = 0

    # the extended facts of each trace (pair)
    extended_facts: dict[str, Any] = field(default_factory=dict)

    # the offset numbers of each request packet
    request_offsets: list[Optional[int]] = field(default_factory=list[Optional[int]])
    # the offset numbers of each response packet that is transmitted the first time
    response_first_offsets: list[Optional[int]] = field(
        default_factory=list[Optional[int]]
    )
    # the offset numbers of each response packet that is a retransmission
    response_retrans_offsets: list[Optional[int]] = field(
        default_factory=list[Optional[int]]
    )

    # The maximum offset value (offset + payload size).
    max_offset: int = field(default=0)

    # The timestamps of packets in `trace.request_stream_packets`
    request_stream_packet_timestamps: list[float] = field(default_factory=list[float])
    # The timestamps of packets in `trace.response_stream_packets`
    response_stream_packet_timestamps: list[float] = field(default_factory=list[float])
    # The timestamps of packets in `trace.response_stream_packets_first_tx`
    response_stream_layers_first_timestamps: list[float] = field(
        default_factory=list[float]
    )
    # The timestamps of packets in `trace.response_stream_packets_retrans`
    response_stream_layers_retrans_timestamps: list[float] = field(
        default_factory=list[float]
    )
    # The timestamps of packets in `trace.server_client_packets`
    server_client_packet_timestamps: list[float] = field(default_factory=list[float])

    # the timestamps of data rate lists
    data_rate_timestamps: Sequence[float] = field(default_factory=list[float])
    # the goodput data rates by trace
    forward_goodput_data_rates: list[float] = field(default_factory=list[float])
    # the transmission data rates by trace
    forward_tx_data_rates: list[float] = field(default_factory=list[float])
    # the return path data rates
    return_data_rates: list[float] = field(default_factory=list[float])

    # the packet numbers of packets in the return direction
    request_packet_numbers: list[int] = field(default_factory=list[int])
    # the packet numbers of packets in the forward direction
    response_packet_numbers: list[int] = field(default_factory=list[int])

    # The payload sizes of the transmitted packets in response direction
    response_transmitted_file_sizes: list[int] = field(default_factory=list[int])
    # The accumulated payload size of the transmitted packets in response direction
    response_accumulated_transmitted_file_sizes: list[int] = field(
        default_factory=list[int]
    )

    # The packet sizes of the packets in response direction
    response_packet_sizes: list[int] = field(default_factory=list[int])
    # The overhead (headers) sizes of the packets in response direction
    response_overhead_sizes: list[int] = field(default_factory=list[int])
    # TODO
    response_stream_data_sizes: list[int] = field(default_factory=list[int])

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TraceAnalyzeResult":
        format_version = data.pop("__FORMAT_VERSION__")
        assert cls.__FORMAT_VERSION__ == format_version
        ret = cls()
        ret.__dict__ = data
        return ret

    def to_json(self) -> dict[str, Any]:
        return self.__dict__

    @cached_property
    def min_timestamp(self) -> float:
        """The minimal packet timestamp we have ever seen."""
        return 0

    @cached_property
    def max_timestamp(self) -> float:
        """The maximum packet timestamp we have ever seen."""
        assert self.extended_facts
        return self.extended_facts["plt"]

    @cached_property
    def max_forward_data_rate(self) -> float:
        """The maximum forward path data rate."""
        return max(*self.forward_tx_data_rates, *self.forward_goodput_data_rates)

    @cached_property
    def max_return_data_rate(self) -> float:
        """The maximum return path data rate."""
        return max(self.return_data_rates)

    @cached_property
    def min_packet_number(self) -> int:
        """The minimum packet number."""
        return min(*self.request_packet_numbers, *self.response_packet_numbers)

    @cached_property
    def max_packet_number(self) -> int:
        """The maximum packet number."""
        return max(*self.request_packet_numbers, *self.response_packet_numbers)

    @cached_property
    def min_response_acc_file_size(self) -> int:
        """The minimum file size."""
        return min(self.response_accumulated_transmitted_file_sizes)

    @cached_property
    def max_response_acc_file_size(self) -> int:
        """The maximum file size."""
        return max(self.response_accumulated_transmitted_file_sizes)

    @cached_property
    def response_packet_stats(self) -> Statistics:
        """Some stats about response packets."""
        return Statistics.calc(self.response_packet_sizes)  # type: ignore

    @cached_property
    def response_overhead_stats(self) -> Statistics:
        """Some stats about overhead data."""
        return Statistics.calc(self.response_overhead_sizes)  # type: ignore

    @cached_property
    def response_stream_data_stats(self) -> Statistics:
        """Some stats about stream data."""
        return Statistics.calc(self.response_stream_data_sizes)  # type: ignore


class PlotCli:
    """Cli for plotting."""

    def __init__(
        self,
        trace_triples: list[TraceTriple],
        title: Union[str, None] = None,
        output_file: Optional[Path] = None,
        annotate=True,
        mode: PlotMode = PlotMode.OFFSET_NUMBER,
        cache=CacheMode.BOTH,
        debug=False,
    ):
        self.output_file: Optional[Path] = output_file
        self.title = title if title else DEFAULT_TITLES[mode] if mode else None
        self.annotate = annotate
        self.mode = mode
        self.debug = debug
        self._markersize = 1
        self.set_params(
            title=title,
            output_file=output_file,
            annotate=annotate,
            mode=mode,
        )
        self._colors = Tango(model="HTML")

        self.traces = list[Trace]()

        for trace_triple in trace_triples:
            left_trace = Trace(
                file=trace_triple.left_pcap_path,
                keylog_file=trace_triple.keylog_path,
                side=Side.LEFT,
                cache=cache,
                debug=self.debug,
            )
            right_trace = Trace(
                file=trace_triple.right_pcap_path,
                keylog_file=trace_triple.keylog_path,
                side=Side.RIGHT,
                cache=cache,
                debug=self.debug,
            )
            right_trace.pair_trace = left_trace
            self.traces.append(right_trace)

        self._analyze_results = list[TraceAnalyzeResult]()

    def analyze_traces(self):
        """Analyze the traces."""
        if self._analyze_results:
            # LOGGER.debug("already analyzed")
            return

        with YaspinWrapper(
            debug=self.debug, text="analyzing...", color="cyan"
        ) as spinner:
            while True:
                try:
                    trace = self.traces.pop()
                except IndexError:
                    break

                result = self.analyze_trace(trace, spinner)
                self._analyze_results.append(result)
                del trace

        self._analyzed = True

    def analyze_trace(self, trace: Trace, spinner):

        cache_file = (
            trace.input_file.parent / f".{trace.input_file.stem}_analyze_cache.json"
        )
        if cache_file.is_file() and cache_file.stat().st_size > 0:
            spinner.write(colored(f"⚒ Loading cache from {cache_file}", color="grey"))
            with cache_file.open() as file:
                try:
                    data = json.load(file)
                    return TraceAnalyzeResult.from_json(data)
                except json.JSONDecodeError as err:
                    spinner.write(f"Could not load cache: {err}")

        with spinner.hidden():
            assert trace.pair_trace
            trace.parse()
            trace.pair_trace.parse()

        DATA_RATE_WINDOW = 1  # 1s

        @dataclass
        class DataRateBufEntry:
            timestamp: float
            raw_data: int
            _successful_stream_data: Optional[int] = None

            @property
            def successful_stream_data(self) -> int:
                assert self._successful_stream_data is not None
                return self._successful_stream_data

        result = TraceAnalyzeResult()

        result.extended_facts = trace.extended_facts

        data_rate_timestamps = np.arange(0, trace.extended_facts["plt"], 0.1).tolist()
        result.data_rate_timestamps = data_rate_timestamps

        # -- offset number --

        for layer in trace.request_stream_packets:
            result.request_stream_packet_timestamps.append(layer.norm_time)

            # packet numbers
            packet_number = int(layer.packet_number)
            result.request_packet_numbers.append(packet_number)

            # offset numbers
            offset = trace.get_stream_offset(layer)
            result.request_offsets.append(offset)

            if offset is not None:
                result.max_offset = max(
                    result.max_offset, offset + trace.get_stream_length(layer)
                )

        for layer in trace.response_stream_packets:
            result.response_stream_packet_timestamps.append(layer.norm_time)

            # packet number

            packet_number = int(layer.packet_number)
            result.response_packet_numbers.append(packet_number)

            # packet sizes (only in direction of response)

            packet_size = int(layer.packet_length)
            result.response_packet_sizes.append(packet_size)
            stream_data_size = trace.get_stream_length(layer)
            result.response_stream_data_sizes.append(stream_data_size)
            result.response_overhead_sizes.append(packet_size - stream_data_size)

        for layer in trace.response_stream_packets_first_tx:
            result.response_stream_layers_first_timestamps.append(layer.norm_time)

            # offset number

            offset = trace.get_stream_offset(layer)
            result.response_first_offsets.append(offset)

            if offset is not None:
                result.max_offset = max(
                    result.max_offset, offset + trace.get_stream_length(layer)
                )

        for layer in trace.response_stream_packets_retrans:
            result.response_stream_layers_retrans_timestamps.append(layer.norm_time)

            # offset number

            offset = trace.get_stream_offset(layer)
            result.response_retrans_offsets.append(offset)
            # Do not calculate result.max_offsets in assertion that retransmitted packets are
            # not larger than previously transmitted packets

        def calc_data_rates(
            data_rate_buf: Sequence[DataRateBufEntry],
            data_rate_timestamps: Sequence[float],
            calc_goodput: bool = True,
        ):
            """Calculate data rates for data_rate_buf."""

            # rate of transmitted data
            tx_data_rates = list[float]()
            # rate of goodput data
            goodput_data_rates = list[float]()

            # marker_start is inclusive, marker_end is exclusive
            marker_start = marker_end = 0

            for timestamp in data_rate_timestamps:
                while data_rate_buf[marker_end].timestamp < timestamp:
                    if marker_end == len(data_rate_buf) - 1:
                        break
                    marker_end += 1

                while (
                    data_rate_buf[marker_start].timestamp < timestamp - DATA_RATE_WINDOW
                ):
                    if marker_start == len(data_rate_buf) - 1:
                        break
                    marker_start += 1

                buf_slice = list(data_rate_buf)[marker_start:marker_end]
                tx_data_rate = (
                    sum(entry.raw_data for entry in buf_slice) / DATA_RATE_WINDOW
                )
                tx_data_rates.append(tx_data_rate)

                if calc_goodput:
                    goodput_data_rate = (
                        sum(entry.successful_stream_data for entry in buf_slice)
                        / DATA_RATE_WINDOW
                    )
                    goodput_data_rates.append(goodput_data_rate)

            return tx_data_rates, goodput_data_rates

        # -- forward data rate --

        data_rate_buf = deque[DataRateBufEntry]()

        for packet in trace.server_client_packets:
            result.server_client_packet_timestamps.append(packet.norm_time)

            # file size (only in response direction)
            file_size = trace.get_quic_payload_size(packet)
            result.response_transmitted_file_sizes.append(file_size)
            acc_file_size = sum(result.response_transmitted_file_sizes)
            result.response_accumulated_transmitted_file_sizes.append(acc_file_size)

            # data rates

            raw_data_len = len(packet.udp.payload.binary_value)
            # goodput
            right_packet = trace.get_pair_packet(packet)

            if not right_packet:
                stream_data_len = 0
            else:
                stream_data_len = trace.pair_trace.get_quic_payload_size(right_packet)

            # *8: convert from byte to bit
            data_rate_buf.append(
                DataRateBufEntry(
                    timestamp=packet.norm_time,
                    raw_data=raw_data_len * 8,
                    _successful_stream_data=stream_data_len * 8,
                )
            )

        (
            result.forward_tx_data_rates,
            result.forward_goodput_data_rates,
        ) = calc_data_rates(data_rate_buf, data_rate_timestamps)

        # -- return path data rates --

        data_rate_buf = deque[DataRateBufEntry]()

        for packet in trace.pair_trace.client_server_packets:
            raw_data_len = len(packet.udp.payload.binary_value)

            # *8: convert from byte to bit
            data_rate_buf.append(
                DataRateBufEntry(
                    timestamp=packet.norm_time,
                    raw_data=raw_data_len * 8,
                )
            )

        # calculate data rates in direction of return path
        result.return_data_rates, _return_goodput_rates = calc_data_rates(
            data_rate_buf,
            data_rate_timestamps,
            calc_goodput=False,
        )

        spinner.write(colored(f"Saving cache file {cache_file}", color="grey"))
        with cache_file.open("w") as file:
            json.dump(
                {
                    **result.__dict__,
                    "__FORMAT_VERSION__": result.__FORMAT_VERSION__,
                },
                file,
            )

        return result

    def set_params(
        self,
        title: Union[str, None] = None,
        output_file: Optional[Path] = None,
        annotate: Optional[bool] = None,
        mode: Optional[PlotMode] = None,
    ):
        self.output_file = output_file

        if mode is not None:
            self.title = title or DEFAULT_TITLES[mode]

        if annotate is not None:
            self.annotate = annotate

        if mode is not None:
            self.mode = mode

    def _vline_annotate(
        self,
        ax,
        x: Union[float, int],
        y: Union[float, int],
        text: str,
        label_side="right",
    ):
        """Annotate with vline."""
        ax.axvline(x=x, color=self._colors.ScarletRed, alpha=0.75)  # , linestyle="--"
        xoffset = 10 if label_side == "right" else -20
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(xoffset, 0),
            textcoords="offset points",
            va="top",
            arrowprops=dict(
                arrowstyle="-",
                color="red",
                alpha=0.75,
            ),
            rotation=90,
            color=self._colors.ScarletRed,
            alpha=0.75,
        )

    def _vdim_annotate(
        self,
        ax,
        left: Union[int, float],
        right: Union[int, float],
        y: Union[int, float],
        text: str,
    ):
        """Add a vertical dimension."""
        ax.annotate(
            "",
            xy=(left, y),
            xytext=(right, y),
            textcoords=ax.transData,
            arrowprops=dict(
                arrowstyle="<->",
                color=self._colors.ScarletRed,
                alpha=0.75,
            ),
            color=self._colors.ScarletRed,
            alpha=0.75,
        )
        ax.annotate(
            "",
            xy=(left, y),
            xytext=(right, y),
            textcoords=ax.transData,
            arrowprops=dict(
                arrowstyle="|-|",
                color=self._colors.ScarletRed,
                alpha=0.75,
            ),
            color=self._colors.ScarletRed,
            alpha=0.75,
        )
        ax.text(
            (right + left) / 2,
            y,
            text,
            ha="center",
            va="center",
            rotation=90,
            color=self._colors.ScarletRed,
            alpha=0.75,
            bbox=dict(fc="white", ec="none"),
        )

    def _annotate_time_plot(
        self, ax: plt.Axes, height: Union[float, int], spinner: YaspinWrapper
    ):
        if not self.annotate:
            return

        if not self._analyze_results[0].extended_facts["is_http09"]:
            spinner.write(
                colored(
                    f"⨯ Can't annotate plot, because HTTP could not be parsed.",
                    color="red",
                )
            )

            return

        ttfb = self._analyze_results[0].extended_facts["ttfb"]
        req_start = self._analyze_results[0].extended_facts["request_start"]
        pglt = self._analyze_results[0].extended_facts["plt"]
        resp_delay = self._analyze_results[0].extended_facts["response_delay"]
        first_resp_tx_time = self._analyze_results[0].extended_facts[
            "first_response_send_time"
        ]
        last_resp_tx_time = self._analyze_results[0].extended_facts[
            "last_response_send_time"
        ]

        for label, value, label_side in (
            (
                f"Req. Start = {req_start:.3f} s",
                req_start,
                "left",
            ),
            (
                f"TTFB = {ttfb:.3f} s",
                ttfb,
                "right",
            ),
            (
                f"Last Resp. TX = {last_resp_tx_time:.3f} s",
                last_resp_tx_time,
                "left",
            ),
            (
                f"PLT = {pglt:.3f} s",
                pglt,
                "right",
            ),
        ):
            self._vline_annotate(
                ax=ax,
                x=value,
                y=height / 2,
                text=label,
                label_side=label_side,
            )

        ax.annotate(  # type: ignore
            f"1st Resp. TX = {first_resp_tx_time:.3f} s",
            xy=(first_resp_tx_time, 0),
            xytext=(-30, -20),
            textcoords="offset points",
            va="top",
            arrowprops=dict(
                arrowstyle="->",
                color="red",
                alpha=0.5,
            ),
            color=self._colors.ScarletRed,
            alpha=0.75,
        )

        self._vdim_annotate(
            ax=ax,
            left=self._analyze_results[0].extended_facts["request_start"],
            right=self._analyze_results[0].extended_facts["ttfb"],
            y=height * 3 / 4,
            text=f"{resp_delay * 1000:.0f} ms",
        )
        end_ts = pglt - last_resp_tx_time
        self._vdim_annotate(
            ax=ax,
            left=last_resp_tx_time,
            right=pglt,
            y=height * 3 / 4,
            text=f"{end_ts * 1000:.0f} ms",
        )

    def plot_offset_number(self, fig, ax, spinner):
        """Plot the offset number diagram."""
        ax.grid(True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Offset")
        assert self.title
        ax.set_title(self.title)
        ax.yaxis.set_major_formatter(lambda val, _pos: naturalsize(val, binary=True))

        # plot shadow traces (request and response separated)
        min_offset: int = 0
        max_offset: int = max(r.max_offset for r in self._analyze_results)

        ax.set_xlim(
            left=min(0, *(r.min_timestamp for r in self._analyze_results)),
            right=max(r.max_timestamp for r in self._analyze_results),
        )
        ax.set_ylim(bottom=min_offset, top=max_offset)
        ax.set_yticks(np.arange(0, max_offset * 1.1, 1024 * 1024))

        for trace_timestamps, trace_offsets in zip(
            (r.request_stream_packet_timestamps for r in self._analyze_results[1:]),
            (r.request_offsets for r in self._analyze_results[1:]),
        ):
            ax.plot(
                trace_timestamps,
                trace_offsets,
                marker="o",
                linestyle="",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        for (
            trace_first_timestamps,
            trace_first_offsets,
            trace_retrans_timestamps,
            trace_retrans_offsets,
        ) in zip(
            (
                r.response_stream_layers_first_timestamps
                for r in self._analyze_results[1:]
            ),
            (r.response_first_offsets for r in self._analyze_results[1:]),
            (
                r.response_stream_layers_retrans_timestamps
                for r in self._analyze_results[1:]
            ),
            (r.response_retrans_offsets for r in self._analyze_results[1:]),
        ):
            ax.plot(
                (*trace_first_timestamps, *trace_retrans_timestamps),
                (*trace_first_offsets, *trace_retrans_offsets),
                marker="o",
                linestyle="",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        # plot main trace (request and response separated)

        ax.plot(
            self._analyze_results[0].request_stream_packet_timestamps,
            self._analyze_results[0].request_offsets,
            marker="o",
            linestyle="",
            color=self._colors.Chameleon,
            markersize=self._markersize,
        )
        ax.plot(
            self._analyze_results[0].response_stream_layers_first_timestamps,
            self._analyze_results[0].response_first_offsets,
            marker="o",
            linestyle="",
            color=self._colors.SkyBlue,
            markersize=self._markersize,
        )
        ax.plot(
            self._analyze_results[0].response_stream_layers_retrans_timestamps,
            self._analyze_results[0].response_retrans_offsets,
            marker="o",
            linestyle="",
            color=self._colors.Orange,
            markersize=self._markersize,
        )

        self._annotate_time_plot(ax, height=max_offset, spinner=spinner)

    def plot_data_rate(self, fig, ax, spinner):
        """Plot the data rate plot."""

        ax.grid(True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Data Rate")
        assert self.title
        ax.set_title(self.title)
        ax.yaxis.set_major_formatter(lambda val, _pos: natural_data_rate(val))

        max_forward_data_rate = max(
            r.max_forward_data_rate for r in self._analyze_results
        )

        ax.set_xlim(
            left=min(0, *(r.min_timestamp for r in self._analyze_results)),
            right=max(r.max_timestamp for r in self._analyze_results),
        )
        ax.set_ylim(bottom=0, top=max_forward_data_rate)
        #  ax.set_yticks(np.arange(0, max_offset * 1.1, 1024 * 1024))

        # plot shadow traces (request and response separated)

        for trace_timestamps, trace_goodput in zip(
            (r.data_rate_timestamps for r in self._analyze_results[1:]),
            (r.forward_goodput_data_rates for r in self._analyze_results[1:]),
        ):
            ax.plot(
                trace_timestamps,
                trace_goodput,
                #  marker="o",
                linestyle="--",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        # plot main trace

        ax.plot(
            self._analyze_results[0].data_rate_timestamps,
            self._analyze_results[0].forward_goodput_data_rates,
            label=r"Goodput (recv'd payload rate delayed by $\frac{-RTT}{2}$)",
            #  marker="o",
            linestyle="--",
            color=self._colors.orange1,
            markersize=self._markersize,
        )
        ax.plot(
            self._analyze_results[0].data_rate_timestamps,
            self._analyze_results[0].forward_tx_data_rates,
            label="Data Rate of Transmitted Packets",
            #  marker="o",
            linestyle="--",
            color=self._colors.orange3,
            markersize=self._markersize,
        )
        ax.legend(loc="upper left", fontsize=8)

        self._annotate_time_plot(ax, height=max_forward_data_rate, spinner=spinner)

    def plot_return_path(self, fig, ax, spinner):
        """Plot the return path utilization."""

        ax.grid(True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Data Rate (Return Path)")
        assert self.title
        ax.set_title(self.title)
        ax.yaxis.set_major_formatter(lambda val, _pos: natural_data_rate(val))
        ax.set_xlim(
            left=min(0, *(r.min_timestamp for r in self._analyze_results)),
            right=max(r.max_timestamp for r in self._analyze_results),
        )
        max_return_data_rate = max(
            r.max_return_data_rate for r in self._analyze_results
        )
        ax.set_ylim(bottom=0, top=max_return_data_rate)
        #  ax.set_yticks(np.arange(0, max_offset * 1.1, 1024 * 1024))

        # plot shadow traces (request and response separated)

        for trace_timestamps, trace_goodput in zip(
            (r.data_rate_timestamps for r in self._analyze_results[1:]),
            (r.return_data_rates for r in self._analyze_results[1:]),
        ):
            ax.plot(
                trace_timestamps,
                trace_goodput,
                #  marker="o",
                linestyle="--",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        # plot main trace

        ax.plot(
            self._analyze_results[0].data_rate_timestamps,
            self._analyze_results[0].return_data_rates,
            label=r"Data Rate in Return Path",
            #  marker="o",
            linestyle="--",
            color=self._colors.orange1,
            markersize=self._markersize,
        )

        self._annotate_time_plot(ax, height=max_return_data_rate, spinner=spinner)

    def plot_packet_number(self, fig, ax, spinner):
        """Plot the packet number diagram."""
        ax.grid(True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Packet Number")
        assert self.title
        ax.set_title(self.title)

        min_packet_number = min(r.min_packet_number for r in self._analyze_results)
        max_packet_number = max(r.max_packet_number for r in self._analyze_results)

        ax.set_xlim(
            left=min(0, *(r.min_timestamp for r in self._analyze_results)),
            right=max(r.max_timestamp for r in self._analyze_results),
        )
        ax.set_ylim(bottom=min(0, min_packet_number), top=max_packet_number)

        # plot shadow traces (request and response separated)

        for trace_timestamps, trace_packet_numbers in zip(
            (r.request_stream_packet_timestamps for r in self._analyze_results[1:]),
            (r.request_packet_numbers for r in self._analyze_results[1:]),
        ):
            ax.plot(
                trace_timestamps,
                trace_packet_numbers,
                marker="o",
                linestyle="",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        for trace_timestamps, trace_packet_numbers in zip(
            (r.response_stream_packet_timestamps for r in self._analyze_results[1:]),
            (r.response_packet_numbers for r in self._analyze_results[1:]),
        ):
            ax.plot(
                trace_timestamps,
                trace_packet_numbers,
                marker="o",
                linestyle="",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        # plot main trace (request and response separated)

        ax.plot(
            self._analyze_results[0].request_stream_packet_timestamps,
            self._analyze_results[0].request_packet_numbers,
            marker="o",
            linestyle="",
            color=self._colors.Plum,
            markersize=self._markersize,
        )
        ax.plot(
            self._analyze_results[0].response_stream_packet_timestamps,
            self._analyze_results[0].response_packet_numbers,
            marker="o",
            linestyle="",
            color=self._colors.SkyBlue,
            markersize=self._markersize,
        )

        self._annotate_time_plot(ax, height=max_packet_number, spinner=spinner)
        spinner.write(f"rtt: {self._analyze_results[0].extended_facts.get('rtt')}")

    def plot_file_size(self, fig, ax, spinner):
        """Plot the file size diagram."""

        ax.grid(True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Transmitted File Size")
        assert self.title
        ax.set_title(self.title)
        ax.yaxis.set_major_formatter(lambda val, _pos: naturalsize(val, binary=True))

        min_response_acc_file_size = min(
            r.min_response_acc_file_size for r in self._analyze_results
        )
        max_response_acc_file_size = max(
            r.max_response_acc_file_size for r in self._analyze_results
        )

        ax.set_xlim(
            left=min(0, *(r.min_timestamp for r in self._analyze_results)),
            right=max(r.max_timestamp for r in self._analyze_results),
        )
        ax.set_ylim(
            bottom=min(0, min_response_acc_file_size),
            top=max_response_acc_file_size,
        )
        ax.set_yticks(np.arange(0, max_response_acc_file_size * 1.1, 1024 * 1024))

        # plot shadow traces

        for trace_timestamps, trace_file_sizes in zip(
            (r.server_client_packet_timestamps for r in self._analyze_results[1:]),
            (
                r.response_accumulated_transmitted_file_sizes
                for r in self._analyze_results[1:]
            ),
        ):
            ax.plot(
                trace_timestamps,
                trace_file_sizes,
                marker="o",
                linestyle="",
                color=self._colors.aluminium4,
                markersize=self._markersize,
            )

        # plot main trace

        ax.plot(
            self._analyze_results[0].server_client_packet_timestamps,
            self._analyze_results[0].response_accumulated_transmitted_file_sizes,
            marker="o",
            linestyle="",
            color=self._colors.SkyBlue,
            markersize=self._markersize,
        )

        self._annotate_time_plot(ax, height=max_response_acc_file_size, spinner=spinner)

    def plot_packet_size(self, fig, ax, spinner):
        """Plot the packet size diagram."""
        ax.grid(True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Packet Size")
        assert self.title
        ax.set_title(self.title)
        ax.yaxis.set_major_formatter(lambda val, _pos: naturalsize(val, binary=True))

        min_timestamp = min(self._analyze_results[0].response_stream_packet_timestamps)
        max_timestamp = max(self._analyze_results[0].response_stream_packet_timestamps)

        ax.set_xlim(left=min(0, min_timestamp), right=max_timestamp)
        #  ax.set_ylim(bottom=0, top=packet_stats.max)
        #  ax.set_yticks(np.arange(0, packet_stats.max * 1.1, 1024))

        # no shadow traces here
        # plot main trace
        ax.stackplot(
            self._analyze_results[0].response_stream_packet_timestamps,
            (
                self._analyze_results[0].response_stream_data_sizes,
                self._analyze_results[0].response_overhead_sizes,
            ),
            colors=(
                self._colors.skyblue1,
                self._colors.plum1,
            ),
            edgecolor=(
                self._colors.skyblue3,
                self._colors.plum3,
            ),
            labels=(
                "Stream Data Size",
                "Overhead Size",
            ),
            baseline="zero",
            step="pre",
        )
        ax.legend(loc="upper left")

        assert self._analyze_results[0].response_packet_stats
        assert self._analyze_results[0].response_stream_data_stats
        assert self._analyze_results[0].response_overhead_stats

        ax.text(
            0.95,
            0.05,
            "\n".join(
                (
                    "Packet Statistics",
                    self._analyze_results[0].response_packet_stats.mpl_label_short(
                        naturalsize
                    ),
                    "\n Stream Data Statistics",
                    self._analyze_results[0].response_stream_data_stats.mpl_label_short(
                        naturalsize
                    ),
                    "\n Overhead Statistics",
                    self._analyze_results[0].response_overhead_stats.mpl_label_short(
                        naturalsize
                    ),
                )
            ),
            transform=ax.transAxes,
            fontsize=12,
            verticalalignment="bottom",
            horizontalalignment="right",
            bbox=dict(
                boxstyle="round",
                facecolor=self._colors.chocolate1,
                edgecolor=self._colors.chocolate3,
                alpha=0.75,
            ),
        )

        self._annotate_time_plot(
            ax,
            height=self._analyze_results[0].response_packet_stats.max,
            spinner=spinner,
        )

    #  def plot_packet_hist(self, output_file: Optional[Path]):
    #      """Plot the packet size histogram."""
    #      with Subplot(ncols=3) as (fig, axs):
    #          assert self.title
    #
    #          for ax in axs:
    #              ax.grid(True)
    #              ax.set_xlabel("Size")
    #              ax.set_ylabel("Amount of Packets")
    #              ax.xaxis.set_major_formatter(
    #                  lambda val, _pos: naturalsize(val, binary=True)
    #              )
    #
    #          with YaspinWrapper(
    #              debug=self.debug, text="processing...", color="cyan"
    #          ) as spinner:
    #              (
    #                  packet_sizes,
    #                  overhead_sizes,
    #                  stream_data_sizes,
    #                  _timestamps,
    #                  _max_timestamp,
    #                  _min_timestamp,
    #                  packet_stats,
    #                  overhead_stats,
    #                  stream_data_stats,
    #              ) = self._process_packet_sizes()
    #
    #          with YaspinWrapper(
    #              debug=self.debug, text="plotting...", color="cyan"
    #          ) as spinner:
    #              fig.suptitle(f"{self.title}\n{packet_stats.num} Packets")
    #              n_bins = 100
    #
    #              axs[0].set_title("Overall Packet Size")
    #              axs[0].hist(
    #                  packet_sizes,
    #                  bins=n_bins,
    #                  color=self._colors.Plum,
    #              )
    #              axs[0].text(
    #                  0.5,
    #                  0.5,
    #                  packet_stats.mpl_label(naturalsize),
    #                  transform=axs[0].transAxes,
    #                  fontsize=10,
    #                  verticalalignment="center",
    #                  horizontalalignment="center",
    #                  bbox=dict(
    #                      boxstyle="round",
    #                      facecolor=self._colors.chocolate1,
    #                      edgecolor=self._colors.chocolate3,
    #                      alpha=0.75,
    #                  ),
    #              )
    #
    #              axs[1].set_title("Overhead Size")
    #              axs[1].hist(
    #                  overhead_sizes,
    #                  bins=n_bins,
    #                  color=self._colors.ScarletRed,
    #              )
    #              axs[1].text(
    #                  0.5,
    #                  0.5,
    #                  stream_data_stats.mpl_label(naturalsize),
    #                  transform=axs[1].transAxes,
    #                  fontsize=10,
    #                  verticalalignment="center",
    #                  horizontalalignment="center",
    #                  bbox=dict(
    #                      boxstyle="round",
    #                      facecolor=self._colors.chocolate1,
    #                      edgecolor=self._colors.chocolate3,
    #                      alpha=0.75,
    #                  ),
    #              )
    #
    #              axs[2].set_title("Stream Data Size")
    #              axs[2].hist(
    #                  stream_data_sizes,
    #                  bins=n_bins,
    #                  color=self._colors.Chameleon,
    #              )
    #              axs[2].text(
    #                  0.5,
    #                  0.5,
    #                  overhead_stats.mpl_label(naturalsize),
    #                  transform=axs[2].transAxes,
    #                  fontsize=10,
    #                  verticalalignment="center",
    #                  horizontalalignment="center",
    #                  bbox=dict(
    #                      boxstyle="round",
    #                      facecolor=self._colors.chocolate1,
    #                      edgecolor=self._colors.chocolate3,
    #                      alpha=0.75,
    #                  ),
    #              )
    #
    #              self._save(fig, output_file, spinner)
    #
    #  def plot_rtt(self, output_file: Optional[Path]):
    #      """Plot the rtt diagram."""
    #      with Subplot() as (fig, ax):
    #          ax.grid(True)
    #          ax.set_xlabel("Time (s)")
    #          ax.set_ylabel("estimated RTT")
    #          assert self.title
    #          ax.set_title(self.title)
    #          ax.yaxis.set_major_formatter(lambda val, _pos: f"{val:.1f} ms")
    #
    #          with YaspinWrapper(
    #              debug=self.debug, text="processing...", color="cyan"
    #          ) as spinner:
    #
    #              for trace in self.traces:
    #                  trace.parse()
    #
    #              self._request_timestamps = [
    #                  [packet.norm_time for packet in trace.request_stream_packets]
    #                  for trace in self.traces
    #              ]
    #              response_timestamps = [
    #                  [packet.norm_time for packet in trace.response_stream_packets]
    #                  for trace in self.traces
    #              ]
    #              request_spin_bits = [
    #                  [
    #                      packet.quic.spin_bit.int_value
    #                      if "spin_bit" in packet.quic.field_names
    #                      else None
    #                      for packet in trace.packets
    #                      if getattr(packet, "direction", None) == Direction.TO_SERVER
    #                  ]
    #                  for trace in self.traces
    #              ]
    #              response_spin_bits = [
    #                  [
    #                      packet.quic.spin_bit.int_value
    #                      if "spin_bit" in packet.quic.field_names
    #                      else None
    #                      for packet in trace.packets
    #                      if getattr(packet, "direction", None) == Direction.TO_CLIENT
    #                  ]
    #                  for trace in self.traces
    #              ]
    #              min_timestamp: float = map3d(
    #                  min, [self._request_timestamps, response_timestamps]
    #              )
    #              max_timestamp: float = max(
    #                  trace.extended_facts["plt"] for trace in self.traces
    #              )
    #
    #              self._request_timestamps = list[list[float]]()
    #              response_timestamps = list[list[float]]()
    #              request_spin_bits = list[list[int]]()
    #              response_spin_bits = list[list[int]]()
    #              min_timestamp = float("inf")
    #              max_timestamp = -float("inf")
    #
    #              for trace in self.traces:
    #                  self._request_timestamps.append(list[float]())
    #                  response_timestamps.append(list[float]())
    #                  request_spin_bits.append(list[int]())
    #                  response_spin_bits.append(list[int]())
    #
    #                  for packet in trace.packets:
    #                      packet_direction = getattr(packet, "direction", None)
    #
    #                      if "spin_bit" not in packet.quic.field_names:
    #                          continue
    #                      spin_bit = packet.quic.spin_bit.int_value
    #                      timestamp = packet.norm_time
    #                      min_timestamp = min(min_timestamp, timestamp)
    #                      max_timestamp = max(max_timestamp, timestamp)
    #
    #                      if packet_direction == Direction.TO_SERVER:
    #                          request_spin_bits[-1].append(spin_bit)
    #                          self._request_timestamps[-1].append(timestamp)
    #                      else:
    #                          response_spin_bits[-1].append(spin_bit)
    #                          response_timestamps[-1].append(timestamp)
    #
    #              ax.set_xlim(left=min(0, min_timestamp), right=max_timestamp)
    #
    #          with YaspinWrapper(
    #              debug=self.debug, text="plotting...", color="cyan"
    #          ) as spinner:
    #              for (
    #                  self._trace_request_timestamps,
    #                  trace_response_timestamps,
    #                  trace_request_spin_bits,
    #                  trace_response_spin_bits,
    #              ) in zip(
    #                  self._request_timestamps[1:],
    #                  response_timestamps[1:],
    #                  request_spin_bits[1:],
    #                  response_spin_bits[1:],
    #              ):
    #                  ax.plot(
    #                      (*self._trace_request_timestamps, *trace_response_timestamps),
    #                      (*trace_request_spin_bits, *trace_response_spin_bits),
    #                      marker="o",
    #                      linestyle="",
    #                      color=self._colors.aluminium4,
    #                      markersize=self._markersize,
    #                  )
    #
    #              # plot main trace (request and response separated)
    #              ax.plot(
    #                  self._request_timestamps[0],
    #                  request_spin_bits[0],
    #                  marker="o",
    #                  linestyle="",
    #                  color=self._colors.Chameleon,
    #                  markersize=self._markersize,
    #              )
    #              ax.plot(
    #                  response_timestamps[0],
    #                  response_spin_bits[0],
    #                  marker="o",
    #                  linestyle="",
    #                  color=self._colors.SkyBlue,
    #                  markersize=self._markersize,
    #              )
    #
    #              self._annotate_time_plot(ax, height=1, spinner=spinner)
    #
    #              self._save(fig, output_file, spinner)

    def _save(
        self, figure: plt.Figure, output_file: Optional[Path], spinner: YaspinWrapper
    ):
        """Save or show the plot."""

        if output_file:
            figure.savefig(
                output_file,
                dpi=300,
                #  transparent=True,
                bbox_inches="tight",
            )
            spinner.text = colored(
                f"{create_relpath(output_file)} written.", color="green"
            )
        else:
            spinner.write(f"✔ {spinner.text}")
            spinner.text = "Showing plot"
            spinner.ok("✔")
            plt.show()

    def run(self):
        """Run command line interface."""

        cprint(f"Plotting {len(self.traces)} traces", color="cyan", attrs=["bold"])
        table = prettytable.PrettyTable()
        table.hrules = prettytable.FRAME
        table.vrules = prettytable.ALL
        table.field_names = [
            colored("left traces", color="cyan", attrs=["bold"]),
            colored("right traces", color="cyan", attrs=["bold"]),
            colored("keylog file", color="cyan", attrs=["bold"]),
        ]

        for i, right_trace in enumerate(self.traces):
            assert right_trace.pair_trace
            table.add_row(
                [
                    colored(
                        str(create_relpath(right_trace.pair_trace.input_file)),
                        attrs=["bold"] if i == 0 else None,
                    ),
                    colored(
                        str(create_relpath(right_trace.input_file)),
                        attrs=["bold"] if i == 0 else None,
                    ),
                    create_relpath(right_trace.keylog_file)
                    if right_trace.keylog_file
                    else colored("-", color="grey"),
                ]
            )

        print(table)

        mapping = {
            PlotMode.OFFSET_NUMBER: {
                "callback": self.plot_offset_number,
            },
            PlotMode.PACKET_NUMBER: {
                "callback": self.plot_packet_number,
            },
            PlotMode.FILE_SIZE: {
                "callback": self.plot_file_size,
            },
            PlotMode.PACKET_SIZE: {
                "callback": self.plot_packet_size,
                "single": True,
            },
            PlotMode.DATA_RATE: {
                "callback": self.plot_data_rate,
            },
            PlotMode.RETURN_PATH: {
                "callback": self.plot_return_path,
            },
            #  PlotMode.SIZE_HIST: {
            #      "callback": self.plot_packet_hist,
            #      "single": True,
            #  },
            #  PlotMode.RTT: {
            #      "callback": self.plot_rtt,
            #  },
        }

        cfg = mapping[self.mode]
        # single: Optional[bool] = cfg.get("single")
        callback = cfg["callback"]
        desc = DEFAULT_TITLES[self.mode]
        # num_traces = 1 if single else len(self.traces)

        # avoid lazy result parsing:

        # if single:
        #     self.traces[0].parse()
        # else:
        # for trace in self.traces:
        #     trace.parse()
        self.analyze_traces()

        cprint(f"⚒ Plotting into a {desc} plot...", color="cyan")

        with YaspinWrapper(
            debug=self.debug, text="plotting...", color="cyan"
        ) as spinner:
            with Subplot() as (fig, ax):
                callback(fig, ax, spinner)
                self._save(fig, self.output_file, spinner)


def main():
    """docstring for main"""
    try:
        args = parse_args()
    except argparse.ArgumentError as err:
        sys.exit(err)

    cli = PlotCli(
        trace_triples=args.trace_triples,
        output_file=args.output_file,
        title=args.title,
        annotate=not args.no_annotation,
        mode=args.mode,
        cache=args.cache,
        debug=args.debug,
    )
    try:
        cli.run()
    except ParsingError as err:
        sys.exit(colored(str(err), color="red"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nQuit")
