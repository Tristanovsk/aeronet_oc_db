# aeronet_oc_db

AERONET-OC (version 3, SeaPRISM) database for the validation of the satellite products of `grs`,
`hgrs`, `grstbx` and `radcalnet_oc`. It replaces the legacy shell tool (`../download_aeronet_OC_v3.sh`)
and does the following:

* **downloads** the data from the AERONET web service (`print_web_data_v3`, all points). It either
  re-downloads the full record of each site, which picks up the reprocessing of past data, or
  fetches only the last days and merges them into the raw file;
* **discovers** the new AERONET-OC sites (sites with data in the last 60 days);
* keeps the **raw files** `<site>_OCv3.lev15|lev20` in the legacy layout. Each file is replaced
  atomically and only when its records changed, so existing readers keep working without changes;
* **builds** the database products of the changed sites:
  * one netCDF-4 file per site and level (CF-1.10 / ACDD-1.3, `featureType=timeSeries`, compressed
    float32), dimensions `(time, wl)`;
  * a Hive-partitioned Parquet dataset (zstd) with one row per (record, band), for queries across
    sites with pandas, pyarrow, duckdb or polars;
  * `catalog.csv`: one row per site and level with the coverage, location, bands, instruments, PI
    and the checksum of the raw file;
* provides **readers** for validation: a dataset per site, filtered tables, and in situ statistics
  at satellite overpass times.

## Install

```bash
pip install git+https://github.com/Tristanovsk/aeronet_oc_db.git
# development (editable) install
git clone https://github.com/Tristanovsk/aeronet_oc_db.git && pip install -e aeronet_oc_db
```

The packages that use it declare it as their `aeronet` extra under its name (`aeronet_oc_db>=1.2`), because
PyPI does not accept dependencies given by a git URL: install `aeronet_oc_db` from GitHub first, then e.g.
`pip install radcalnet_oc[aeronet]`.

## Update

```bash
aeronet-oc-db update --discover              # full re-download of all sites, new sites, build
aeronet-oc-db update --days 30               # quick update: last 30 days merged into the raw files
aeronet-oc-db update --sites AAOT Venise --levels lev20
aeronet-oc-db build --force                  # rebuild the products from the raw files
aeronet-oc-db discover                       # active sites, flags the ones not in the database
aeronet-oc-db status                         # catalog
```

The root folder is `$AERONET_OC_ROOT`, or `/DATA/AERONET/OCv3` by default (`--root` overrides it).
Each run is logged in `db/update.log`.

Level 2.0 data are quality-assured with a delay of months to years and past records get
reprocessed. Use `--days` for frequent updates, and run a full update (the default) from time to
time, e.g. monthly.

## Location of the database (shared by all the packages)

The packages find the database without hardcoded paths, in this order:

1. `$AERONET_OC_ROOT`;
2. `root` in `~/.config/aeronet_oc_db/config.toml` (also read by Jupyter, PyCharm and cron, which do not
   always inherit the shell environment):
   ```toml
   root = "/DATA/AERONET/OCv3"
   ```
3. `/DATA/AERONET/OCv3`.

## Use from the other packages

`aeronet_oc_db` is the single access layer to the AERONET-OC data. Each package declares it as an
optional dependency (`aeronet` extra), imports it only in the functions that need it, and keeps a
fallback when it is missing (CI, users without the database):

| package | install | entry points |
|---|---|---|
| radcalnet_oc | `pip install radcalnet_oc[aeronet]` | `Aeronet().read_site(site, level)`, `Aeronet.open_site`, `Aeronet().aeronet_oc_site_info` |
| grstbx | `pip install grstbx[aeronet]` | `Data().read_aeronet_oc_site`, `Data.open_aeronet_oc`, `Data.aeronet_oc_sites` |
| invRrs | `pip install invRrs[aeronet]` | `aeronet_data_loading.read().read_site`, `read.open_site`, `read.sites` |
| hgrs | validation scripts | `validation/aeronet_oc/common.py` (`HGRS_VAL_AERONET` overrides the location) |
| aeronet_visu | | `aeronet_files_merger.py` |

Functions of `aeronet_oc_db` for the code of the packages:

```python
import aeronet_oc_db
aeronet_oc_db.open_site('AAOT', 'lev20')                    # xarray.Dataset (time, wl), database names
aeronet_oc_db.open_site('AAOT', 'lev20', legacy_names=True) # names of the former netcdf/OCv3 files
aeronet_oc_db.raw_file('AAOT', 'lev20')                     # raw file, for the existing text readers
aeronet_oc_db.site_info()                                   # location, Sentinel-2 tile, coverage per level
aeronet_oc_db.site_info(level='lev15')                      # columns of info_aeronet_oc_v15_location.csv
aeronet_oc_db.default_root()                                # location of the database
```

`legacy_names=True` gives the variable names of the former `AERONET/netcdf/OCv3` conversion
(`Aerosol_Optical_Depth`, `Lwn_fQ`, `Rrs_Lwn*`, `'Total_Precipitable_Water(cm)'`, `wavelength` in um,
`SZA`, ...), so that existing code only changes the line that opens the data. New code should use the
database names.

The Sentinel-2 tile of each site comes from `aeronet_oc_db/data/site_s2_tile.csv` (checked by hand: the
stations in the overlap of two tiles, e.g. Venise and AAOT in 32TQR rather than 33TUL), or is computed
(MGRS 100 km square) for the new sites. Add a line to that file to force the tile of a site.

## Layout

```
OCv3/
  <site>_OCv3.lev15, <site>_OCv3.lev20                 raw files (legacy layout: header on line 8)
  db/catalog.csv
  db/netcdf/<level>/<site>_OCv3.<level>.nc
  db/parquet/level=<level>/site=<site>/data.parquet
  db/update.log
```

## Variables

| name | dims | units | AERONET field |
|---|---|---|---|
| `wl` | coord | nm | nominal band |
| `wl_exact` | time, wl | nm | `Exact_Wavelengths(um)` |
| `Lt`, `Lt_std`, `Lt_min_rel` | time, wl | uW cm-2 nm-1 sr-1 | `Lt_mean`, `Lt_stddev`, `Lt_min_rel` |
| `Li`, `Li_std` | time, wl | uW cm-2 nm-1 sr-1 | `Li_mean`, `Li_stddev` |
| `rho` | time, wl | 1 | `Rho` |
| `Lw`, `LwQ`, `Lwn`, `Lwn_fQ`, `Lwn_IOP` | time, wl | uW cm-2 nm-1 sr-1 | `Lw`, `LwQ`, `Lwn`, `Lwn_f/Q`, `Lwn_IOP` |
| `Rrs_Lwn`, `Rrs_Lwn_fQ`, `Rrs_Lwn_IOP` | time, wl | sr-1 | 10 Lwn / F0 (see below) |
| `aot`, `tau_rayleigh`, `tau_o3`, `tau_no2`, `tau_h2o` | time, wl | 1 | `*_Optical_Depth` |
| `sza_band`, `saa_band` | time, wl | degree | `Solar_Zenith_Angle`, `Solar_Azimuth_Angle` |
| `sza` | time | degree | mean of `sza_band` |
| `tco3`, `tno2` | time | DU | `Total_Ozone(Du)`, `Total_NO2(DU)` |
| `tcwv` | time | cm | `Total_Precipitable_Water(cm)` |
| `chl` | time | mg m-3 | `Chlorophyll-a` |
| `wind`, `pressure` | time | m s-1, hPa | `Wind_Speed(m/s)`, `Pressure(hPa)` |
| `raa_sea`, `vza_sea` | time | degree | `ASea(Degrees)`, `ZSea(Degrees)` |
| `instrument`, `quality_level`, `last_processed` | time | | instrument number, data level, processing date |
| `latitude`, `longitude`, `elevation`, `station_name` | scalar | | site |

`Rrs_* = 10 Lwn_* / F0(wl_exact)`. F0 is the Thuillier et al. (2003) spectrum averaged over a
Gaussian band of 10 nm FWHM centred on the exact wavelength, as in `hgrs/validation/aeronet_oc`.
The previous `AERONET/netcdf/OCv3` files used the 0.3 nm spectrum interpolated at the exact
wavelength, which gives up to ±4% differences at 412 and 443 nm because of the Fraunhofer lines.
Times are UTC.

## Use in the validation

```python
from aeronet_oc_db import Database
db = Database()

# one site (xarray)
ds = db.open('AAOT', 'lev20')
ds.Rrs_Lwn_fQ.sel(time='2023-07').plot.line(x='wl', add_legend=False)

# in situ data at satellite overpasses: median of the records within 1 h
mu = db.match('AAOT', overpass_times, level='lev20', max_lag='1h')      # dims (match, wl)
mu.Rrs_Lwn_fQ, mu.aot, mu.n_records, mu.lag_min

# several sites, filtered at read time (pandas)
df = db.table('lev20', sites=['AAOT', 'Venise', 'Galata_Platform'], start='2018-01-01',
              columns=['Rrs_Lwn_fQ', 'aot', 'sza', 'chl'], wl=[443, 490, 560, 665])

# sites with a long level 2.0 record
db.catalog.query("level == 'lev20' and n_records > 1000")
```

The products can be read without this package:

```python
import xarray as xr, pandas as pd
xr.open_dataset('/DATA/AERONET/OCv3/db/netcdf/lev20/AAOT_OCv3.lev20.nc')
pd.read_parquet('/DATA/AERONET/OCv3/db/parquet', filters=[('level', '==', 'lev20'), ('site', '==', 'AAOT')])
# duckdb: SELECT * FROM read_parquet('/DATA/AERONET/OCv3/db/parquet/*/*/*.parquet', hive_partitioning=true)
```

## Tests

```bash
~/anaconda3/envs/py12/bin/python -m pytest tests
```

## Releasing on PyPI

The workflow `.github/workflows/pypi.yml` builds the sdist and the wheel, checks them (`twine check`, version,
content), runs the tests on the installed wheel with Python 3.10 and 3.12, then publishes with PyPI trusted
publishing (no token stored).

One-time setup, on pypi.org and test.pypi.org (*Your projects > Publishing > Add a new pending publisher*):
owner `Tristanovsk`, repository `aeronet_oc_db`, workflow `pypi.yml`, environment `pypi` (resp.
`testpypi`). In the GitHub repository, create the environments `pypi` and `testpypi`
(*Settings > Environments*; a required reviewer on `pypi` adds a manual approval before each upload).

Release:

1. set `__version__` in `aeronet_oc_db/__init__.py` (the only place, `pyproject.toml` reads it);
2. *Actions > Publish on PyPI > Run workflow* with `testpypi`, then check
   `pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ aeronet_oc_db`;
3. tag and push: `git tag -a v<version> -m "aeronet_oc_db <version>" && git push origin v<version>`: the
   workflow publishes on PyPI (the tag must match `__version__`).

Once on PyPI, `pip install radcalnet_oc[aeronet]` (or grstbx, invRrs) installs `aeronet_oc_db` with no
extra step.

## Data policy

AERONET-OC data are public. Publications must acknowledge AERONET and the site PIs (the PI is
stored in the attributes of each file and in the catalog). See
https://aeronet.gsfc.nasa.gov/new_web/data_usage.html.
