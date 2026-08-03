"""
Author: eveie Prewitt
Initial analysis of shipping presence in the Strait of Hormuz.
"""
import matplotlib.pyplot as plt
import geopandas as gpd
import shapely.geometry as box

def ship_snapshot(snapshot_file_name: str):
    """

    """
    # Load the snapshot data
    snapshot = gpd.read_file(snapshot_file_name)

    # Visualize the snapshot
    snapshot.plot()
    plt.show()



def main():
    pass

if __name__ == "__main__":
    main()