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

    world = geopandas.read_file(geopandas.datasets.get_path("naturalearth_lowres"))

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
import geopandas
import matplotlib
import pandas

try:
    import geopandas as gpd
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    import pandas as pd
    from shapely.geometry import box
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
TYPE_COLOURS = {
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

    lat_col = next((c for c in ("lat", "position.lat", "latitude") if c in frame), None)
    lon_col = next((c for c in ("lon", "position.lon", "longitude") if c in frame), None)
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

    def __init__(self, bbox: regions.BBox=regions.STRAIT_OF_HORMUZ,
                 cache_dir: str=CACHE_DIR, pad: float=0.1) -> None:
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
        self.borders = self._clipped("ne_10m_countries", BORDERS_URL, cache_dir)
        self._figure = None
        self._axes = None

    def _clipped(self, name: str, url: str, cache_dir: str) -> geopandas.GeoDataFrame:
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

    def base(self, figsize: tuple[float, float]=(11, 9)) -> matplotlib.axes.Axes:
        """
        Start a figure with the coastline drawn, ready for data on top.

        Args:
            figsize (tuple[float, float]): Figure size in inches. Defaults to
                (11, 9).

        Returns:
            matplotlib.axes.Axes: The axes to draw onto.
        """
        self._figure, self._axes = plt.subplots(figsize=figsize)
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

    def density(self, gdf: geopandas.GeoDataFrame, value: str="hours",
                title: str | None=None, cmap: str="YlOrRd", log: bool=True) -> matplotlib.axes.Axes:
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
            title (str | None): Figure title. Generated when omitted.
            cmap (str): Matplotlib colour map. Defaults to "YlOrRd".
            log (bool): Use a logarithmic colour scale. Defaults to True.

        Returns:
            matplotlib.axes.Axes: The axes drawn onto.
        """
        axes = self.base()
        cells = self._cells(gdf, value)
        cells = cells[cells[value] > 0]

        norm = None
        if log and not cells.empty:
            norm = LogNorm(vmin=max(cells[value].min(), 0.1), vmax=cells[value].max())

        cells.plot(ax=axes, column=value, cmap=cmap, alpha=0.85, zorder=3,
                   norm=norm, legend=True,
                   legend_kwds={"label": f"total {value} (log scale)" if log
                                else f"total {value}", "shrink": 0.6})
        self._coastline_on_top()
        axes.set_title(title or
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

    def _cells(self, gdf: geopandas.GeoDataFrame, value: str) -> geopandas.GeoDataFrame:
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
        frame["cell_lon"] = frame.geometry.x.round(2)
        frame["cell_lat"] = frame.geometry.y.round(2)
        totals = frame.groupby(["cell_lon", "cell_lat"], as_index=False)[value].sum()

        size = self._cell_size(totals)
        squares = [box(lon, lat, lon + size, lat + size)
                   for lon, lat in zip(totals["cell_lon"], totals["cell_lat"])]
        return gpd.GeoDataFrame(totals, geometry=squares, crs=gdf.crs)

    def _compare_to_peacetime(self, gdf: geopandas.GeoDataFrame,
                              peacetime: pandas.DataFrame, value: str="hours") -> geopandas.GeoDataFrame:
        """
        Compare a gridded presence file to a peacetime baseline.

        Args:
            gdf (geopandas.GeoDataFrame): Rows from a `presence --grid` file.
            peacetime (pandas.DataFrame): The baseline, as written by
                `main.py peacetime`.
            value (str): Column to total per cell. Defaults to "hours".

        Returns:
            geopandas.GeoDataFrame: One square per cell, with the summed
                value minus the peacetime baseline. Cells with no difference
                are dropped.
        """
        cells = self._cells(gdf, value)
        merged = cells.merge(peacetime, how="left", on=["cell_lon", "cell_lat"],
                             suffixes=("", "_baseline"))
        merged[value + "_baseline"] = merged[value + "_baseline"].fillna(0)
        merged["difference"] = merged[value] - merged[value + "_baseline"]
        return merged[merged["difference"] != 0]

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
        return min(g for g in gaps if g > 0) if any(g > 0 for g in gaps) else 0.1

    def points(self, gdf: geopandas.GeoDataFrame, colour_by: str="ship_type",
               title: str | None=None, size: float=14) -> matplotlib.axes.Axes:
        """
        Draw one point per vessel or event.

        Args:
            gdf (geopandas.GeoDataFrame): Rows with point geometry.
            colour_by (str): Column to colour by, if present. Defaults to
                "ship_type"; falls back to a single colour when absent.
            title (str | None): Figure title. Generated when omitted.
            size (float): Marker size. Defaults to 14.

        Returns:
            matplotlib.axes.Axes: The axes drawn onto.
        """
        axes = self.base()

        if colour_by in gdf.columns:
            for name, group in gdf.groupby(colour_by):
                group.plot(ax=axes, markersize=size, zorder=3,
                           color=TYPE_COLOURS.get(name, "#c0392b"),
                           label=f"{name} ({len(group)})",
                           edgecolor="white", linewidth=0.3)
            axes.legend(loc="upper right", fontsize=8, framealpha=0.9)
        else:
            gdf.plot(ax=axes, markersize=size, color="#c0392b", zorder=3,
                     edgecolor="white", linewidth=0.3)

        self._coastline_on_top()
        axes.set_title(title or f"{len(gdf):,} vessels")
        return axes

    def save(self, path: str, dpi: int=150) -> None:
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


def draw(csv_path: str, out_path: str | None=None,
         region: regions.BBox=regions.STRAIT_OF_HORMUZ, show: bool=False) -> str:
    """
    Read a CSV and draw whichever kind of map suits it.

    Args:
        csv_path (str): A CSV written by main.py.
        out_path (str | None): Image to write. Defaults to the CSV's name
            with a .png extension.
        region (regions.BBox): Area to map. Defaults to the Strait of Hormuz.
        show (bool): Open an interactive window as well. Defaults to False.

    Returns:
        str: The path written.
    """
    gdf = load_csv(csv_path)
    region_map = RegionMap(region)

    if "hours" in gdf.columns:
        region_map.density(gdf)
    else:
        region_map.points(gdf)

    out_path = out_path or os.path.splitext(csv_path)[0] + ".png"
    region_map.save(out_path)
    if show:
        plt.show()
    return out_path
