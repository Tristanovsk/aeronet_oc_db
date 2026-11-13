'''
AERONET-OC version 3 database: download and update of the raw files of the AERONET web service,
conversion into CF/ACDD netCDF-4 and Hive-partitioned Parquet, catalog and readers for the
validation of satellite products (grs, hgrs, grstbx, radcalnet_oc).
'''
__version__ = '1.0.0'

from .database import Database, DEFAULT_ROOT  # noqa: E402,F401
from .raw import RawFile, parse  # noqa: E402,F401
