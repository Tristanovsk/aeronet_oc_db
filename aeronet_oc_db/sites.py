'''
Site metadata that the AERONET files do not provide: Sentinel-2 tile (MGRS) of the stations.

The tiles of ``data/site_s2_tile.csv`` (checked by hand, the station can be in the overlap of two tiles)
take precedence; for the other sites the MGRS 100 km square of the station is computed.
'''
from functools import lru_cache
from importlib.resources import files

import numpy as np
import pandas as pd

_LAT_BANDS = 'CDEFGHJKLMNPQRSTUVWX'
_COL_SETS = ('ABCDEFGH', 'JKLMNPQR', 'STUVWXYZ')
_ROW_LETTERS = 'ABCDEFGHJKLMNPQRSTUV'


def mgrs_tile(lon, lat):
    '''MGRS grid zone and 100 km square (Sentinel-2 tile name, e.g. ``'33TUL'``) of a point
    (WGS84, latitudes -80 to 84 degrees, with the Norway and Svalbard zone exceptions)'''
    from pyproj import Transformer
    if not -80 <= lat < 84:
        raise ValueError('MGRS is defined from 80S to 84N')
    zone = int((lon + 180) // 6) + 1
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84 and lon >= 0:
        for z, (lo, hi) in {31: (0, 9), 33: (9, 21), 35: (21, 33), 37: (33, 42)}.items():
            if lo <= lon < hi:
                zone = z
    band = _LAT_BANDS[min(int((lat + 80) // 8), 19)]
    epsg = (32600 if lat >= 0 else 32700) + zone
    e, n = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    col = _COL_SETS[(zone - 1) % 3][int(e // 100000) - 1]
    row = _ROW_LETTERS[(int(n // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    return f'{zone:02d}{band}{col}{row}'


@lru_cache()
def curated_tiles():
    return pd.read_csv(files('aeronet_oc_db.data').joinpath('site_s2_tile.csv'), index_col='site').s2_tile.to_dict()


def s2_tile(site, lon, lat):
    tile = curated_tiles().get(site)
    if tile:
        return tile
    return mgrs_tile(lon, lat) if np.isfinite(lon) and np.isfinite(lat) else ''
