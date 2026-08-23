"""
Plotting data for analysis purposes.
"""
import os
import pandas as pd
import matplotlib.pyplot as plt

US_belligerent = ["USA", "ISR", "SAU", "ARE", "KWT", "BHR", "YEM"]
Iran_belligerent = ["IRN"]


def vessel_departures_per_day(csv_path: str,
                              beligerents_only: bool,
                              plot_title: str | None = None,
                              show: bool = False) -> str:
    """
    Plots the counts of unique vessels that depart an area.
    Has the ability to show only conflict participants.

    Args:
        csv_path (str): A CSV written by main.py.
        beligerents_only (bool): choice to only show conflict participants.
        plot_title (str): Optional user generated plot title. Defaults to
            false.
        show (bool): Open an interactive window as well. Defaults to False.
    """
    presence = pd.read_csv(csv_path)

    # Use the timestamp column in the dataset.
    timestamp_column = next(
        column for column in ("timestamp", "time", "datetime", "date")
        if column in presence.columns
    )

    presence[timestamp_column] = pd.to_datetime(
        presence[timestamp_column],
        errors="coerce",
        utc=True,
    )
    presence = presence.dropna(subset=[timestamp_column, "vessel_id"])
    presence["exit_day"] = presence[timestamp_column].dt.floor("D")

    min_day = presence["exit_day"].min().date()
    max_day = presence["exit_day"].max().date()
    start_of_conflict = pd.Timestamp("2026-02-28", tz="UTC")

    presence = presence[
        presence["exit_day"].dt.date < max_day
    ]

    if beligerents_only:
        groups = [
            (
                "US-aligned vessels",
                presence[presence["flag"].isin(US_belligerent)]
            ),
            (
                "Iranian vessels",
                presence[presence["flag"].isin(Iran_belligerent)]
            ),
        ]

        figure, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)

        for axis, (title, group) in zip(axes, groups):
            departures = (
                group.groupby(["exit_day", "flag"], as_index=False)
                .agg(departures=("vessel_id", "nunique"))
            )

            counts = departures.pivot(
                index="exit_day",
                columns="flag",
                values="departures",
            ).fillna(0)

            for flag in counts.columns:
                axis.plot(counts.index, counts[flag], label=flag)
            if (start_of_conflict.date() > min_day
                    and start_of_conflict.date() < max_day):
                axis.axvline(
                    start_of_conflict,
                    color="red",
                    linestyle="--",
                    linewidth=1.5,
                    label="February 28, 2026",
                )
            axis.set_title(title)
            axis.set_ylabel("Unique vessels present")
            axis.legend(title="Flag")

        axes[-1].set_xlabel("Date")
        figure.suptitle(
            plot_title or f"Vessel presence from {min_day} to {max_day}"
        )
        figure.tight_layout()

        out_path = "vessel_per_day_" + os.path.splitext(csv_path)[0] + ".png"
        figure.savefig(out_path, bbox_inches="tight", dpi=300)

    else:
        departures = (
            presence.groupby(["exit_day", "flag"],
                             as_index=False).agg(departures=("vessel_id",
                                                             "nunique"))
        )
        counts = departures.pivot(
            index="exit_day",
            columns="flag",
            values="departures"
        ).fillna(0)

        # Plot the counts of unique vessels by flag over time
        lineplot = plt.figure(figsize=(12, 6))
        for flag in counts.columns:
            plt.plot(counts.index, counts[flag], label=flag)
        if (start_of_conflict.date() > min_day
                and start_of_conflict.date() < max_day):
            plt.axvline(
                start_of_conflict,
                color="red",
                linestyle="--",
                linewidth=1.5,
                label="February 28, 2026",
            )

        plt.xlabel("Date")
        plt.ylabel("Unique vessels present")
        plt.title(plot_title or f"Vessel presence by flag from {min_day}"
                  f" to {max_day}")
        plt.legend(title="Flag", bbox_to_anchor=(1.02, 1), loc="upper left")

        out_path = "vessel_per_day_" + os.path.splitext(csv_path)[0] + ".png"

        lineplot.savefig(out_path, bbox_inches="tight", dpi=300)

    if show:
        plt.show()

    plt.close()
    print(f"wrote {out_path}")
    return out_path
