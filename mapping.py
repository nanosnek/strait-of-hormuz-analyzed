"""
Draw the collected vessel data on a map of the region, using GeoPandas.

Works with any of the CSV files this project writes, and figures out which
kind it is from the columns present:

  gridded presence   has lat, lon and hours   -> a density map, one coloured
                     square per grid cell, darker where more vessel-hours
                     were spent. This is `main.py presence --grid`.
  live snapshot      has lat, lon and speed_knots -> a point per vessel,
                     coloured by ship type. This is `main.py snapshot`.
  events             has position.lat / position.lon -> a point per event.

One thing worth knowing before you follow any tutorial: **geopandas.datasets
was removed in GeoPandas 1.0**, so the usual

    world =
    geopandas.read_file(geopandas.datasets.get_path("naturalearth_lowres"))

raises AttributeError on any current install. This module downloads the same
Natural Earth data straight from its CDN instead, and caches it under
basemap/ so it is only fetched once.

Setup:
    pip install -r requirements.txt

Usage:
    python main.py map presence_hormuz_2026-04-30_2026-07-29.csv
    python main.py map snapshot.csv -o vessels.png
"""

import math
import os
import sys

import geopandas
import matplotlib
import pandas
import seaborn

try:
    import geopandas as gpd
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    import pandas as pd
    from shapely.geometry import box
    from shapely.geometry import LineString
    import seaborn as sns

except ImportError:
    sys.exit("mapping.py needs geopandas and matplotlib:  "
             "pip install -r requirements.txt")

import regions

# Natural Earth, straight from its CDN. 10m ("large scale") rather than the
# usual 110m because the Strait of Hormuz is only a couple of degrees across
# and the low-resolution coastline is too coarse to recognise at that size.
LAND_URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"
BORDERS_URL = ("https://naciscdn.org/naturalearth/10m/cultural/"
               "ne_10m_admin_0_countries.zip")

CACHE_DIR = "basemap"

# Colours for the live-snapshot point map, by MarineTraffic ship type name.
TYPE_COLORS = {
    "Tanker": "#c0392b",
    "Cargo": "#e67e22",
    "Passenger": "#2980b9",
    "Tug & Special Craft": "#16a085",
    "Fishing": "#8e44ad",
    "Pleasure Craft": "#7f8c8d",
    "High Speed Craft": "#f1c40f",
    "Navigation Aid": "#34495e",
    "Unspecified": "#bdc3c7",
}

US_BELLIGERENT = ["USA", "ISR", "SAU", "ARE", "KWT", "BHR", "YEM"]
IRAN_BELLIGERENT = ["IRN"]


def load_csv(path: str) -> geopandas.GeoDataFrame:
    """
    Read one of this project's CSV files into a GeoDataFrame.

    Finds the latitude and longitude columns whatever they are called -- the
    presence and snapshot files use lat/lon, while the events files nest them
    as position.lat / position.lon -- and builds point geometry from them.

    Args:
        path (str): Path to a CSV written by main.py.

    Returns:
        geopandas.GeoDataFrame: The rows, with a `geometry` column of points
            in EPSG:4326 (plain longitude/latitude degrees). Rows with no
            position are dropped.

    Raises:
        SystemExit: If no latitude/longitude columns can be found, which
            usually means the file was written without --grid.
    """
    frame = pd.read_csv(path)

    lat_col = next((c for c in ("lat", "position.lat", "latitude")
                    if c in frame), None)
    lon_col = next((c for c in ("lon", "position.lon", "longitude")
                    if c in frame), None)
    if not lat_col or not lon_col:
        sys.exit(
            f"{path} has no latitude/longitude columns (found: "
            f"{', '.join(list(frame.columns)[:8])}...).\n"
            "A presence file only has coordinates if you passed --grid:\n"
            "  python main.py presence --days 90 --grid --group-by VESSEL_ID"
        )

    frame = frame.dropna(subset=[lat_col, lon_col])
    return gpd.GeoDataFrame(
        frame,
        geometry=gpd.points_from_xy(frame[lon_col], frame[lat_col]),
        crs="EPSG:4326",
    )


class RegionMap:
    """
    A map of one region, that vessel data can be drawn onto.

    Holds the basemap (coastline and borders, clipped to the region) so it is
    downloaded and clipped once even if several layers get drawn on top.

    Attributes:
        bbox (regions.BBox): The area being mapped.
        land (geopandas.GeoDataFrame): Land polygons clipped to the region.
        borders (geopandas.GeoDataFrame): Country outlines clipped to region.
    """

    def __init__(self, bbox: regions.BBox = regions.STRAIT_OF_HORMUZ,
                 cache_dir: str = CACHE_DIR, pad: float = 0.1) -> None:
        """
        Args:
            bbox (regions.BBox): Area to map. Defaults to the Strait of
                Hormuz.
            cache_dir (str): Where to keep the downloaded Natural Earth data,
                so it is fetched once rather than every run. Defaults to
                "basemap".
            pad (float): Extra degrees drawn beyond the box, so vessels near
                the edge aren't right up against the frame. Defaults to 0.1.
        """
        self.bbox = bbox
        self.pad = pad
        self.land = self._clipped("ne_10m_land", LAND_URL, cache_dir)
        self.borders = self._clipped("ne_10m_countries",
                                     BORDERS_URL, cache_dir)
        self._figure = None
        self._axes = None

    def _clipped(self, name: str, url: str,
                 cache_dir: str) -> geopandas.GeoDataFrame:
        """
        Load one Natural Earth layer, clipped to this region.

        Downloads on first use and caches the clipped result, which is a few
        kilobytes rather than the tens of megabytes of the full dataset.

        Args:
            name (str): Cache file stem, e.g. "ne_10m_land".
            url (str): Where to fetch the layer if it isn't cached.
            cache_dir (str): Directory to cache into.

        Returns:
            geopandas.GeoDataFrame: The layer, clipped to the padded region.
        """
        os.makedirs(cache_dir, exist_ok=True)
        cached = os.path.join(cache_dir, f"{name}_{self._slug()}.geojson")
        if os.path.exists(cached):
            return gpd.read_file(cached)

        print(f"  downloading {name} (first run only)...", file=sys.stderr)
        layer = gpd.read_file(url)
        clipped = gpd.clip(layer, box(*self._padded_bounds()))
        if not clipped.empty:
            clipped.to_file(cached, driver="GeoJSON")
        return clipped

    def _slug(self) -> str:
        """
        A short filename-safe label for this region's bounds.

        Returns:
            str: e.g. "55.2_25.4_57.4_27.3", so different regions don't share
                a cache file.
        """
        b = self.bbox
        return f"{b.west}_{b.south}_{b.east}_{b.north}".replace("-", "m")

    def _padded_bounds(self) -> tuple[float, float, float, float]:
        """
        The region bounds with `pad` degrees added on every side.

        Returns:
            tuple[float, float, float, float]: (west, south, east, north).
        """
        b, p = self.bbox, self.pad
        return (b.west - p, b.south - p, b.east + p, b.north + p)

    def base(self,
             figsize: tuple[float, float] = (11, 9),
             ax: matplotlib.axes.Axes | None = None) -> matplotlib.axes.Axes:
        """
        Start a figure with the coastline drawn, ready for data on top.

        Args:
            figsize (tuple[float, float]): Figure size in inches. Defaults to
                (11, 9).
            ax (matplotlib.axes.Axes): Draws figure onto a given axes if using
                a subplot in a different function.

        Returns:
            matplotlib.axes.Axes: The axes to draw onto.
        """
        if ax is None:
            self._figure, self._axes = plt.subplots(figsize=figsize)
        else:
            self._axes = ax
            self._figure = ax.figure

        west, south, east, north = self._padded_bounds()

        # Sea first, then land over it, so the coastline reads correctly.
        self._axes.set_facecolor("#dce9f2")
        if not self.land.empty:
            self.land.plot(ax=self._axes, color="#efe8dc", edgecolor="#9aa8a0",
                           linewidth=0.6, zorder=1)
        if not self.borders.empty:
            self.borders.boundary.plot(ax=self._axes, color="#b0a99a",
                                       linewidth=0.5, linestyle="--", zorder=2)

        self._axes.set_xlim(west, east)
        self._axes.set_ylim(south, north)
        self._axes.set_xlabel("longitude")
        self._axes.set_ylabel("latitude")
        # Degrees of longitude shrink towards the poles; without this the map
        # comes out horizontally stretched.
        mid = (south + north) / 2
        self._axes.set_aspect(1 / max(0.1, abs(math.cos(math.radians(mid)))))
        return self._axes

    def density(self, gdf: geopandas.GeoDataFrame, value: str = "hours",
                plot_title: str | None = None,
                cmap: str = "YlOrRd",
                log: bool = True,
                ax: matplotlib.axes.Axes | None = None
                ) -> matplotlib.axes.Axes:
        """
        Draw gridded presence as coloured cells.

        Sums `value` for every distinct grid cell, so a file covering 90 days
        becomes one total per cell rather than 90 overlapping points.

        Traffic is very unevenly spread -- the busiest cell off Bandar Abbas
        holds well over a hundred times what a quiet cell in open water does
        -- so the colour scale is logarithmic by default. On a linear scale
        the port swamps everything and the whole strait reads as empty.

        Args:
            gdf (geopandas.GeoDataFrame): Rows from a `presence --grid` file.
            value (str): Column to total per cell. Defaults to "hours".
            plot_title (str | None): Figure title. Generated when omitted.
            cmap (str): Matplotlib colour map. Defaults to "YlOrRd".
            log (bool): Use a logarithmic colour scale. Defaults to True.
            ax (matplotlib.axes.Axes): supplies axes if adding to a subplot
                in a seperate function.

        Returns:
            matplotlib.axes.Axes: The axes drawn onto.
        """
        axes = self.base(ax=ax)

        if "cell_lon" in gdf.columns and "cell_lat" in gdf.columns:
            cells = gdf.copy()
        else:
            cells = self._cells(gdf, value)
        cells = cells[cells[value] > 0]

        norm = None
        if log and not cells.empty:
            norm = LogNorm(vmin=max(cells[value].min(), 0.1),
                           vmax=cells[value].max())

        cells.plot(ax=axes, column=value, cmap=cmap, alpha=0.85, zorder=3,
                   norm=norm, legend=True,
                   legend_kwds={"label": f"total {value} (log scale)" if log
                                else f"total {value}", "shrink": 0.6})
        self._coastline_on_top()
        axes.set_title(plot_title or
                       f"Vessel presence, {value} per grid cell "
                       f"({len(gdf):,} records)")
        return axes

    def _coastline_on_top(self) -> None:
        """
        Redraw the coastline above the data.

        Without this the semi-transparent data layer sits over the shoreline
        and the map stops being recognisable as a place.

        Returns:
            None
        """
        if not self.land.empty:
            self.land.boundary.plot(ax=self._axes, color="#5b6b62",
                                    linewidth=0.9, zorder=5)

    def _cells(self, gdf: geopandas.GeoDataFrame,
               value: str) -> geopandas.GeoDataFrame:
        """
        Turn gridded points into square cell polygons totalling `value`.

        Args:
            gdf (geopandas.GeoDataFrame): Rows carrying lat/lon and `value`.
            value (str): Column to sum.

        Returns:
            geopandas.GeoDataFrame: One square per cell, with the summed
                value. Cell size is inferred from the spacing in the data.
        """
        frame = gdf.copy()
        # The API returns float32 coordinates, so 55.8 arrives as
        # 55.79999923706055. Round before grouping or every row looks like
        # its own distinct cell.
        frame["cell_lon"] = frame.geometry.x.round(1)
        frame["cell_lat"] = frame.geometry.y.round(1)
        totals = frame.groupby(["cell_lon", "cell_lat"],
                               as_index=False)[value].sum()

        size = self._cell_size(totals)
        squares = [box(lon, lat, lon + size, lat + size)
                   for lon, lat in zip(totals["cell_lon"], totals["cell_lat"])]
        return gpd.GeoDataFrame(totals, geometry=squares, crs=gdf.crs)

    def _compare_to_peacetime(self, gdf: geopandas.GeoDataFrame,
                              peacetime: geopandas.GeoDataFrame,
                              value: str = "hours", cmap: str = "YlOrRd",
                              log: bool = True) -> geopandas.GeoDataFrame:
        """
        Compare a gridded presence GeoDataFrame to a peacetime baseline to
        produce a new GeoDataFrame with the difference in presence.

        Args:
            gdf (geopandas.GeoDataFrame): Rows from a `presence --grid` file.
            peacetime (geopandas.GeoDataFrame): The baseline, as written by
                `main.py peacetime`.
            value (str): Column to total per cell. Defaults to "hours".
            log (bool): Use a logarithmic colour scale. Defaults to True.

        Returns:
            geopandas.GeoDataFrame: One square per cell, with the summed
                value and a new column "difference" = value - peacetime.
        """
        # Calculate the total presence for the current data
        current_totals = self._cells(gdf, value)
        # Calculate the total presence for the peacetime baseline
        peacetime_totals = self._cells(peacetime, value)

        comparison = current_totals.merge(peacetime_totals,
                                          on=["cell_lon", "cell_lat"],
                                          how="outer",
                                          suffixes=('_current', '_peacetime'))
        comparison.set_geometry(comparison.geometry_current, inplace=True)
        comparison["difference"] = (
            comparison[f"{value}_current"] - comparison[f"{value}_peacetime"]
        )

        return comparison

    @staticmethod
    def _cell_size(totals: pandas.DataFrame) -> float:
        """
        Work out the grid spacing from the data itself.

        Args:
            totals (pandas.DataFrame): Must have a cell_lon column.

        Returns:
            float: The spacing in degrees, defaulting to 0.1 (the API's LOW
                spatial resolution) when it can't be inferred.
        """
        unique = sorted(totals["cell_lon"].unique())
        if len(unique) < 2:
            return 0.1
        gaps = [round(b - a, 4) for a, b in zip(unique, unique[1:])]
        if any(g > 0 for g in gaps):
            return min(g for g in gaps if g > 0)
        else:
            return 0.1

    def points(
            self,
            gdf: geopandas.GeoDataFrame,
            color_by: str = "ship_type",
            plot_title: str = None,
            size: float = 14
    ) -> matplotlib.axes.Axes:
        """
        Draw one point per vessel or event.

        Args:
            gdf (geopandas.GeoDataFrame): Rows with point geometry.
            color_by (str): Column to color by, if present. Defaults to
                "ship_type"; falls back to a single color when absent.
            title (str | None): Figure title. Generated when omitted.
            size (float): Marker size. Defaults to 14.

        Returns:
            matplotlib.axes.Axes: The axes drawn onto.
        """
        axes = self.base()

        if color_by in gdf.columns:
            unique_vals = list(gdf[color_by].dropna().unique())
            if len(unique_vals) == 0:
                # nothing to plot
                gdf.plot(ax=axes, markersize=size, color="#c0392b",
                         zorder=3, edgecolor="white", linewidth=0.3)
            else:
                # Build a color map: use TYPE_COLORS for ship_type, otherwise
                # a palette
                if len(unique_vals) == 1:
                    color_map = {name:
                                 TYPE_COLORS.get(name, "#c0392b")
                                 for name in unique_vals}
                else:
                    palette = sns.color_palette("turbo",
                                                n_colors=len(unique_vals))
                    color_map = {name: palette[i % len(palette)] for i,
                                 name in enumerate(unique_vals)}

                for name, group in gdf.groupby(color_by):
                    col = color_map.get(name, "#c0392b")
                    group.plot(ax=axes,
                               markersize=size,
                               zorder=3,
                               color=col,
                               label=f"{name} ({len(group)})",
                               edgecolor="white", linewidth=0.3)
                axes.legend(loc="upper right", fontsize=8, framealpha=0.9)
        else:
            gdf.plot(ax=axes, markersize=size, color="#c0392b",
                     zorder=3, edgecolor="white", linewidth=0.3)

        self._coastline_on_top()
        axes.set_title(plot_title or f"{len(gdf):,} vessels by {color_by}")
        return axes

    def lines(self,
              gdf: geopandas.GeoDataFrame,
              plot_title: str = None) -> matplotlib.axes.Axes:
        """
        Draws a path per vessel given mulitiple vessel detections.

        Args:
            gdf (geopandas.GeoDataFrame): Rows with point geometry.
            plot_title (str | None): Figure title. Generated when omitted.

        Returns:
            matplotlib.axes.Axes: The axes drawn onto.
        """
        gdf = gdf.sort_values(by=['ship_id', 'observed_at'])
        axes = self.base()

        grouped = gdf.groupby('ship_id')['geometry']
        paths = (
            grouped
            .apply(lambda pts: LineString(pts.tolist())
                   if len(pts) > 1 else None)
            .reset_index(name='geometry')
        )
        # Drop entries where a LineString couldn't be built
        # (single-point tracks)
        paths = paths.dropna(subset=['geometry'])
        paths = gpd.GeoDataFrame(paths, geometry='geometry')
        paths.plot(ax=axes,
                   column='ship_id',
                   cmap='Set1',
                   linewidth=2)
        self._coastline_on_top()
        axes.set_title(plot_title or f"{len(paths):,} vessel paths")
        return axes

    def save(self, path: str, dpi: int = 150) -> None:
        """
        Write the current figure to an image file.

        Args:
            path (str): Output path; the extension picks the format (.png,
                .pdf, .svg).
            dpi (int): Resolution for raster formats. Defaults to 150.

        Returns:
            None
        """
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._figure.savefig(path, dpi=dpi, bbox_inches="tight")
        print(f"wrote {path}", file=sys.stderr)


def draw(
    csv_path: str,
    out_path: str | None = None,
    color_by: str = "ship_type",
    region: regions.BBox = regions.STRAIT_OF_HORMUZ,
    plot_title: str | None = None,
    show: bool = False,
) -> str:
    """
    Read a CSV and draw whichever kind of map suits it.

    Args:
        csv_path (str): A CSV written by main.py.
        out_path (str | None): Image to write. Defaults to the CSV's name
            with a .png extension.
        color_by (str): Option to give a column name to color points by
            unique groups.
        region (regions.BBox): Area to map. Defaults to the Strait of Hormuz.
        plot_title (str): Tile is optional and given by the user. Default is
            given in mapping functions.
        show (bool): Open an interactive window as well. Defaults to False.

    Returns:
        str: The path to a plot.
    """
    gdf = load_csv(csv_path)
    region_map = RegionMap(region)

    if "hours" in gdf.columns:
        region_map.density(gdf, value="hours", plot_title=plot_title)
    else:
        region_map.points(gdf, color_by=color_by, plot_title=plot_title)

    out_path = out_path or os.path.splitext(csv_path)[0] + ".png"
    region_map.save(out_path)
    if show:
        plt.show()
    return out_path


def density_of_beligerents(csv_path: str,
                           out_path: str | None = None,
                           region: regions.BBox = regions.STRAIT_OF_HORMUZ,
                           plot_title: str | None = None,
                           show: bool = False) -> str:
    """
    Reads a CSV of presence data and returns density plots of vessels
    associated with belligerent actions in the Strait.
    Args:
        csv_path (str): A CSV written by main.py.
        out_path (str | None): Image to write. Defaults to the CSV's name
            with a .png extension.
        region (regions.BBox): Area to map. Defaults to the Strait of Hormuz.
        plot_title (str): Tile is optional and given by the user. Default is
            given in mapping functions.
        show (bool): Open an interactive window as well. Defaults to False.

    Returns:
        str: The path to a plot.
    """
    gdf = load_csv(csv_path)
    region_map = RegionMap(region)

    us_gdf = gdf[gdf['flag'].isin(US_BELLIGERENT)]
    us_plot_title = "US Allied Presence"

    iran_gdf = gdf[gdf['flag'].isin(IRAN_BELLIGERENT)]
    iran_plot_title = "Iranian Presence"

    fig, [ax1, ax2] = plt.subplots(2, 1, figsize=(11, 16))
    region_map.density(gdf=us_gdf, plot_title=us_plot_title, ax=ax1)
    region_map.density(gdf=iran_gdf, plot_title=iran_plot_title, ax=ax2)

    fig.text(
        0.5,
        0.99,
        plot_title or
        f"Belligerent Vessel Presence in the {region.get_name()} from \n"
        f"{gdf['date'].min()} to {gdf['date'].max()}",
        ha="center",
        va="top",
        fontsize=16,
        rotation=0,
    )
    fig.subplots_adjust(top=0.94, hspace=0.25)
    out_path = out_path or os.path.splitext(csv_path)[0] + "_belligerents.png"
    fig.savefig(out_path, dpi=150, bbox_inches='tight')

    if show:
        plt.show()
    plt.close(fig)
    return out_path


def draw_path(csv_paths: list[str],
              out_path: str | None = None,
              region: regions.BBox = regions.STRAIT_OF_HORMUZ,
              plot_title: str | None = None,
              show: bool = False) -> str:
    """
    Draws the paths of vessels given several 'snapshot' CSVs.

    Args:
        csv_paths list[str]: A list of 'snapshot' CSV paths written by main.py
        out_path (str | None): Image to write. Defaults to the CSV's name
            with a .png extension.
        region (regions.BBox): Area to map. Defaults to the Strait of Hormuz.
        plot_title (str): Tile is optional and given by the user. Default is
            ''.
        show (bool): Open an interactive window as well. Defaults to False.

    Returns:
        str: The path to a map in .png format.
    """
    if len(csv_paths) <= 1:
        sys.exit(
            "Not enough data for path finding. "
            "Please supply multiple snapshots by running "
            "'python main.py watch --interval 900' and passing the "
            "resulting paths into draw_path in list format."
        )
    gdf = load_csv(csv_paths[0])
    if "observed_at" not in gdf.columns:
        sys.exit(
            "The files provided to draw_path must be snapshot CSVs containing "
            "an 'observed_at' column."
        )
    for csv_path in csv_paths[1:]:
        g = load_csv(csv_path)
        if "observed_at" not in g.columns:
            sys.exit(
                f"{csv_path} is not a snapshot CSV and is missing the "
                "'observed_at' column."
            )
        gdf = pd.concat([gdf, g])

    region_map = RegionMap(region)
    starting = gdf['observed_at'].min()
    ending = gdf['observed_at'].max()

    if plot_title is None:
        plot_title = (f"Vessel paths in {region.get_name()} "
                      f"from {starting} to {ending}")

    region_map.lines(gdf, plot_title, )

    # Build a filesystem-safe output filename (avoid characters like ':'
    # on Windows)
    start_s = str(starting).replace(":", "-")
    end_s = str(ending).replace(":", "-")
    out_path = (
        out_path or
        f"paths_{region.get_name().replace(' ', '_')}_{start_s}_to_{end_s}.png"
    )
    region_map.save(out_path)
    if show:
        plt.show()
    return out_path


def compare_to_peacetime(csv_path: str,
                         out_path: str | None = None,
                         region: regions.BBox = regions.STRAIT_OF_HORMUZ,
                         show: bool = False) -> str:
    """
    Read a gridded presence CSV and draw the difference from a peacetime
    baseline.

    Args:
        csv_path (str): A CSV written by main.py presence --grid.
        peacetime (geopandas.GeoDataFrame): The baseline, as written by
            `main.py peacetime`.
        out_path (str | None): Image to write. Defaults to the CSV's name
            with a .png extension.
        region (regions.BBox): Area to map. Defaults to the Strait of Hormuz.
        show (bool): Open an interactive window as well. Defaults to False.
    Returns:
        str: The path written.
    """
    if not os.path.exists("peacetime_hormuz_2022-01-01_2022-12-31.csv"):
        sys.exit(
            "Peacetime baseline file not found. Please run "
            "'python main.py presence --peacetime --grid"
            "--group-by VESSEL_ID' first."
        )

    gdf = load_csv(csv_path)
    gdf_peacetime = load_csv(
        "peacetime_hormuz_2022-01-01_2022-12-31.csv"
    )
    # mask peacetime data to match presence data range
    gdf['month_day'] = pd.to_datetime(gdf['date']).dt.strftime('%m-%d')
    gdf_peacetime['month_day'] = (
        pd.to_datetime(gdf_peacetime['date']).dt.strftime('%m-%d')
    )
    start = gdf['month_day'].min()
    end = gdf['month_day'].max()
    gdf_peacetime = gdf_peacetime[
        (gdf_peacetime['month_day'] >= start)
        & (gdf_peacetime['month_day'] <= end)]
    region_map = RegionMap(region)
    diff_gdf = region_map._compare_to_peacetime(gdf, gdf_peacetime)
    if diff_gdf.empty:
        sys.exit(
            "No overlapping grid cells were found between the current CSV and "
            "the peacetime baseline. Check that both files were generated as "
            "gridded presence CSVs."
        )
    plot_title = (f"Comparing the vessel presence in {region.get_name()} from "
                  f"{start} to {end} 2026 to peacetime levels")
    region_map.density(diff_gdf, value="difference",
                       plot_title=plot_title)

    out_path = out_path or os.path.splitext(csv_path)[0] + "_diff.png"
    region_map.save(out_path)
    if show:
        plt.show()
    return out_path
