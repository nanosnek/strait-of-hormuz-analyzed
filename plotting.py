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

    if beligerents_only:
        presence = presence[
            (presence['flag'].isin(US_belligerent + Iran_belligerent))]

    presence["exit_day"] = presence[timestamp_column].dt.floor("D")
    max_day = presence["exit_day"].max()
    presence = presence[
        presence["exit_day"] < max_day
        ]  # drop the trailing censored day

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

    plt.xlabel("Date")
    plt.ylabel("Unique vessels present")
    plt.title(plot_title or "Vessel presence by flag over time ")
    plt.legend(title="Flag", bbox_to_anchor=(1.02, 1), loc="upper left")

    out_path = "vessel_per_day_" + os.path.splitext(csv_path)[0] + ".png"

    lineplot.savefig(out_path, bbox_inches="tight", dpi=300)

    if show:
        plt.show()

    plt.close(lineplot)
    print(f"wrote {out_path}")
    return out_path
