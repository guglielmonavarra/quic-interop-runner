#!/usr/bin/env python3

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt
from termcolor import colored

from result_parser import MeasurementDescription, Result
from tango_colors import Tango
from utils import Subplot, natural_data_rate

TIMESTAMPS_CSV = Path("experiment-datetimes.csv")


PGF_PREAMBLE = r"""
\usepackage{acronym}
\usepackage{lmodern}
\usepackage{helvet}
\usepackage[bitstream-charter,sfscaled=false]{mathdesign}
% More encoding and typesetting fixes and tweaks
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{textcomp}
% \input{latex_cmds}
\usepackage{datetime2}

\RequirePackage[%
	binary-units,
    % range-phrase={--},
	range-units=single,
    per-mode=symbol,
    detect-all,
    load-configurations=binary,
    forbid-literal-units,
]{siunitx}
\catcode`\%=12\relax
\DeclareSIUnit[number-unit-product=]\percent{%}
\catcode`\%=14\relax

\def\lr/{\mbox{\textsc{LongRTT}}}
\def\g/{\mbox{\textsc{Goodput}}}
\def\sat/{\mbox{\textsc{Sat}}}
\def\satl/{\mbox{\textsc{SatLoss}}}
\def\eut/{\mbox{\textsc{Eutelsat}}}
\def\astra/{\mbox{\textsc{Astra}}}
\def\crosstraffic/{\mbox{\textsc{CrossTraffic}}}
"""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "result",
        type=Result,
        help="The result file to use",
    )
    parser.add_argument(
        "--measurement",
        nargs="+",
        type=str,
        default=["AST", "EUT"],
        help="The measurements to use",
    )
    parser.add_argument(
        "-o",
        "--img-path",
        dest="img_path",
        action="store",
        type=Path,
        default=Path(__file__).parent,
        help="The directory to render the images to.",
    )
    parser.add_argument(
        "-n",
        "--no-interactive",
        action="store_true",
        help="Do not open plot preview.",
    )
    parser.add_argument(
        "-f",
        "--format",
        type=str,
        default="png",
        help="The format of the image to save",
    )

    return parser.parse_args()


class PlotSatCli:
    def __init__(
        self,
        result: Result,
        measurements: list[str],
        img_path: Path,
        img_format: str,
        # debug: bool = False,
        no_interactive: bool = False,
    ):
        self.result = result
        self._meas_abbrs = measurements
        self.img_path = img_path
        self.img_format = img_format
        self._colors = Tango()
        self.no_interactive = no_interactive

        if self.img_format in {"tex", "pgf", "pdf"}:
            self.tex_mode = True
            sns.set(
                "paper",
                "white",
                rc={
                    # "font.size": 10,
                    # "axes.labelsize": 10,
                    # "legend.fontsize": 8,
                    # "axes.titlesize": 10,
                    # "xtick.labelsize": 8,
                    # "ytick.labelsize": 8,
                    "font.family": "serif",
                    "font.serif": [],
                    "pgf.rcfonts": False,
                },
            )
            plt.rcParams.update(
                {
                    "text.usetex": True,
                    "font.family": "serif",
                    #  # don't setup fonts from rc parameters
                    "pgf.rcfonts": False,
                    # Use LaTeX default serif font.
                    "font.serif": [],
                    # "font.sans-serif": [],
                    "pgf.texsystem": "pdflatex",
                    "pgf.preamble": PGF_PREAMBLE,
                }
            )
        else:
            sns.set("paper", "white")
            self.tex_mode = False

    @property
    def measurements(self) -> list[MeasurementDescription]:
        """The measurements to use."""

        try:
            return [
                self.result.measurement_descriptions[abbr] for abbr in self._meas_abbrs
            ]
        except KeyError:
            sys.exit(
                f"Unknown measurement in {', '.join(self._meas_abbrs)}. "
                f"Known ones are: {', '.join(sorted(self.result.measurement_descriptions.keys()))}"
            )

    def run(self):
        data = self.collect_data()
        self.plot_data(data)

    def _save(self, figure: plt.Figure, output_file_base_name: str):
        """Save or show the plot."""

        output_file = self.img_path / f"{output_file_base_name}.{self.img_format}"
        # assert self._spinner
        figure.savefig(
            output_file,
            dpi=300,
            #  transparent=True,
            bbox_inches="tight",
        )
        text = colored(f"{output_file} written.", color="green")
        print(text)
        # self._spinner.write(f"✔ {text}")
        if not self.no_interactive:
            # self._spinner.text = "Showing plot"
            print("Showing plot")
            plt.show()

    def plot_data(self, df: pd.DataFrame):
        def format_data_rate(val, _pos=None):
            value = natural_data_rate(val)
            if self.tex_mode:
                number, unit = value.split(" ")
                unit = {
                    "bit/s": r"\bit\per\second",
                    "kbit/s": r"\kilo\bit\per\second",
                    "Mbit/s": r"\mega\bit\per\second",
                    "Gbit/s": r"\giga\bit\per\second",
                }[unit]
                value = fr"\SI{{{number}}}{{{unit}}}"

            return value

        def format_date_time(val, _pos=None):
            return fr"\DTMdate{{{val}}}"

        with Subplot(ncols=2, sharey=True) as (fig, axs):
            assert not isinstance(axs, plt.Axes)
            [ax1, ax2] = axs
            assert isinstance(ax1, plt.Axes)
            assert isinstance(ax2, plt.Axes)

            # ax1.yaxis.tick_right()
            ax1.yaxis.set_major_formatter(format_data_rate)
            ax1.yaxis.set_label_coords(1, y=1)
            ax1.yaxis.label.set_rotation(0)
            #  ax1.xaxis.set_major_formatter(format_date_time)

            #  fig.suptitle("Measurement Results using Real Satellite Links over Time")

            # cmap = sns.color_palette(as_cmap=True)

            # mark pause
            pause_start_time = time(18, 0)
            pause_end_time = time(23, 0)
            min_dt = df["Time"].min()
            max_dt = df["Time"].max()
            pause_date = min_dt.date()
            while datetime.combine(pause_date, pause_start_time) <= max_dt:
                ax1.axvspan(
                    xmin=datetime.combine(pause_date, pause_start_time),
                    xmax=datetime.combine(pause_date, pause_end_time),
                    color=self._colors.aluminium1,
                )
                pause_date = pause_date + timedelta(hours=24)

            ax2.axvspan(
                xmin=datetime(1970, 1, 1, 18, 0),
                xmax=datetime(1970, 1, 1, 23, 0),
                color=self._colors.aluminium1,
            )

            sns.scatterplot(
                data=df,
                x="Time",
                y="Goodput",
                hue="Measurement",
                edgecolors="white",
                linewidth=0.5,
                legend=True,
                ax=ax1,
            )
            sns.scatterplot(
                data=df,
                x="Time of Day",
                y="Goodput",
                hue="Measurement",
                edgecolors="white",
                linewidth=0.5,
                legend=False,
                ax=ax2,
            )

            # ax2.xaxis.set_major_locator(mdates.HourLocator())
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            ax2.set_xlim(xmin=datetime(1970, 1, 1, 0, 0), xmax=datetime(1970, 1, 2))

            ax1.set_title("Time")
            ax2.set_title("Time of Day")

            for label in (*ax1.get_xticklabels(), *ax2.get_xticklabels()):
                label.set_rotation(45)

            # lines, labels = fig.axes[-1].get_legend_handles_labels()
            # fig.legend(
            #     lines,
            #     labels,
            #     title="Measurement",
            #     loc="lower center",
            #     ncol=3,
            #     bbox_to_anchor=(0.5, -0.2),
            # )
            # ax1.legend(
            #     title="Measurement",
            #     loc="lower left",
            #     bbox_to_anchor=(1.1, -0.2),
            #     ncol=3,
            #     fancybox=False,
            #     shadow=False,
            # )

            ax1.set(xlabel=None)
            ax2.set(xlabel=None)
            ax1.set(ylabel=None)

            # determine regression
            drop = {
                "eutelsat": {
                    "Time": {
                        "first": 3,
                        "last": 33,
                    },
                    "Time of Day": {
                        "first": 0,
                        "last": 0,
                    },
                },
                "astra": {
                    "Time": {
                        "first": 2,
                        "last": 7,
                    },
                    "Time of Day": {
                        "first": 0,
                        "last": 0,
                    },
                },
            }
            for meas in df["Measurement"].unique():
                # ... for each measurement
                for (ax, col, resample_freq) in (
                    (ax1, "Time", "12h"),
                    (ax2, "Time of Day", "1h"),
                ):
                    drop_first = drop[meas][col]["first"]
                    drop_last = drop[meas][col]["last"]
                    # ... for each subplot with a different frequency
                    data = (
                        # select measurement
                        df[df["Measurement"] == meas]
                        # select index and filter columns
                        .set_index(col)["Goodput"]
                        # sort by index (time)
                        .sort_index()
                        # drop first and last measurements that are odd
                        .iloc[drop_first : -drop_last - 1]
                        # make equidistant time steps
                        .resample(resample_freq)
                        # aggregate by using means
                        .mean()
                        # convert back to data frame and drop time ranges without values
                        .reset_index().dropna()
                    )
                    # use these integer indices for fitting
                    fit_xs = data.index
                    # fit by using int indices and goodputs
                    fit = np.polyfit(
                        x=fit_xs,
                        y=data["Goodput"],
                        deg=2,
                    )
                    fit_fn = np.poly1d(fit)
                    # determine x values to plot in the fit (datetime and numeric values)
                    plot_xs = pd.date_range(
                        start=df[col].min(), end=df[col].max(), freq=resample_freq
                    )
                    plot_xs_index = plot_xs.map(
                        lambda date: (date - data[col].iloc[0])
                        / (data[col].iloc[-1] - data[col].iloc[0])
                    )
                    # plot_xs = data[col]
                    # plot_xs_index = data.index
                    # calculate y values for given numeric x values
                    ys = fit_fn(plot_xs_index)
                    # plot regression
                    sns.lineplot(
                        x=plot_xs,
                        y=ys,
                        linestyle="--",
                        ax=ax,
                    )

            meas_abbrs = "-".join(meas.abbr for meas in self.measurements)
            self._save(
                fig,
                f"real-sat-experiment-results-{meas_abbrs}",
            )

    def collect_data(self) -> pd.DataFrame:

        if TIMESTAMPS_CSV.is_file():
            df = pd.read_csv(TIMESTAMPS_CSV)
            del df["Unnamed: 0"]
            df["Time"] = pd.to_datetime(df["Time"], format="%Y-%m-%d %H:%M:%S")
            df["Time of Day"] = pd.to_datetime(
                df["Time of Day"], format="%Y-%m-%d %H:%M:%S"
            )
        else:
            data = list[tuple[datetime, datetime, float, str]]()

            for meas_desc in self.measurements:
                for meas in self.result.get_all_measurements_of_type(meas_desc.abbr):
                    for i, value in enumerate(meas.values):
                        output = meas.repetition_log_dirs[i] / "output.txt"

                        if output.is_file():
                            with output.open("r") as file:
                                first_line = file.readline()
                                date = datetime.fromisoformat(first_line.split(",")[0])
                            time = date.replace(year=1970, month=1, day=1)
                            data.append((date, time, value * 1000, meas.test.name))

            df = pd.DataFrame(
                data,
                columns=["Time", "Time of Day", "Goodput", "Measurement"],
            )

            if self.use_time:
                df["Time"] = pd.to_datetime(df["Time"], format="%H:%M:%S")
                df["Time"] = pd.to_timedelta(df["Time"], unit="s")

        return df


def main():
    args = parse_args()

    result = args.result
    result.load_from_json()

    cli = PlotSatCli(
        result=result,
        measurements=args.measurement,
        img_path=args.img_path,
        img_format=args.format,
        no_interactive=args.no_interactive,
    )
    cli.run()


if __name__ == "__main__":
    main()
