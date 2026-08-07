"""
The geographic areas this project studies.

Shared by both data sources so the boxes are defined once: gfw.py (Global
Fishing Watch, the main source) and livemap.py (MarineTraffic live positions).
"""

from dataclasses import dataclass


@dataclass
class BBox:
    """
    A rectangular geographic area, in degrees.

    West must be less than east, so a box straddling the +/-180 antimeridian
    can't be expressed. None of the regions here need one -- split such a box
    into two if you ever do.

    Attributes:
        west (float): Western edge, in degrees longitude (-180 to 180).
        south (float): Southern edge, in degrees latitude (-90 to 90).
        east (float): Eastern edge, in degrees longitude. Must exceed west.
        north (float): Northern edge, in degrees latitude. Must exceed south.
    """

    west: float
    south: float
    east: float
    north: float

    def contains(self, lon, lat):
        """
        Test whether a point falls inside this box.

        Edges count as inside, so a point exactly on a shared border belongs
        to both neighbouring boxes.

        Args:
            lon (float): Longitude of the point, in degrees.
            lat (float): Latitude of the point, in degrees.

        Returns:
            bool: True if the point is inside the box or on its edge.
        """
        return self.west <= lon <= self.east and self.south <= lat <= self.north

    def to_geojson(self):
        """
        Convert this box to a GeoJSON Polygon.

        This is how Global Fishing Watch accepts a custom area. Note two
        GeoJSON conventions that are easy to get wrong: coordinates are
        [longitude, latitude] pairs, the opposite order from how we usually
        say "lat/lon", and the ring must close by repeating the first point
        at the end.

        Returns:
            dict: A GeoJSON Polygon geometry, with keys "type" and
                "coordinates". The coordinate ring has five points -- the
                box's four corners, counter-clockwise from the south-west,
                plus the south-west corner again to close it.
        """
        return {
            "type": "Polygon",
            "coordinates": [[
                [self.west, self.south],
                [self.east, self.south],
                [self.east, self.north],
                [self.west, self.north],
                [self.west, self.south],
            ]],
        }


# The chokepoint itself: Bandar Abbas / Qeshm across to Khasab and the TSS.
STRAIT_OF_HORMUZ = BBox(55.20, 25.40, 57.40, 27.30)
# Wider basins, matching the area_local_in=25|Persian Gulf, 41|Oman Gulf
# filter that data_get.py uses against the reports endpoint.
PERSIAN_GULF = BBox(47.50, 23.50, 56.60, 30.50)
GULF_OF_OMAN = BBox(56.00, 22.00, 61.50, 27.20)
ALL_REGIONS = BBox(47.50, 22.00, 61.50, 30.50)

REGIONS = {
    "hormuz": STRAIT_OF_HORMUZ,
    "persian-gulf": PERSIAN_GULF,
    "oman-gulf": GULF_OF_OMAN,
    "all-regions": ALL_REGIONS,
}
