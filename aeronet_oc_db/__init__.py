'''
AERONET-OC version 3 database: download and update of the raw files of the AERONET web service,
conversion into CF/ACDD netCDF-4 and Hive-partitioned Parquet, catalog and readers for the
validation of satellite products (grs, hgrs, grstbx, radcalnet_oc).
'''
__version__ = '1.1.0'

from .database import Database, DEFAULT_ROOT  # noqa: E402,F401
from .raw import RawFile, parse  # noqa: E402,F401
from .config import default_root  # noqa: E402,F401


# shortcuts on the default database (location: see :mod:`aeronet_oc_db.config`)
def open_site(site, level='lev20', **kwargs):
    '''netCDF dataset (time, wl) of a site, see :meth:`Database.open`'''
    return Database().open(site, level, **kwargs)


def raw_file(site, level='lev20'):
    '''path of the raw AERONET-OC file of a site (legacy text format)'''
    return Database().raw_path(site, level)


def site_info():
    '''one row per site: location, Sentinel-2 tile and coverage, see :meth:`Database.site_info`'''
    return Database().site_info()


def catalog():
    '''one row per site and level, see :attr:`Database.catalog`'''
    return Database().catalog
