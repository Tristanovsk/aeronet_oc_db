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
~/anaconda3/envs/py12/bin/pip install -e /DATA/AERONET/TOOL/aeronet_oc_db
```

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

## Data policy

AERONET-OC data are public. Publications must acknowledge AERONET and the site PIs (the PI is
stored in the attributes of each file and in the catalog). See
https://aeronet.gsfc.nasa.gov/new_web/data_usage.html.
