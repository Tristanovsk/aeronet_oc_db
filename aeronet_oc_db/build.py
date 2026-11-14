'''
Conversion of the raw AERONET-OC files into the database products:

* ``db/netcdf/<level>/<site>_OCv3.<level>.nc``: CF-1.10 / ACDD-1.3 netCDF-4 (compressed), dimensions
  ``(time, wl)``, one file per site and level, for xarray;
* ``db/parquet/level=<level>/site=<site>/data.parquet``: Hive-partitioned Parquet dataset (zstd),
  one row per (record, band), for pandas / pyarrow / duckdb queries across the sites.
'''
import hashlib
import os

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr

from . import __version__
from .raw import SCALAR, SPECTRAL, parse
from .solar import F0_SOURCE, FWHM, f0

RRS_FROM = {'Rrs_Lwn': 'Lwn', 'Rrs_Lwn_fQ': 'Lwn_fQ', 'Rrs_Lwn_IOP': 'Lwn_IOP'}
ATTRS = {v[0]: dict(units=v[1], long_name=v[2], **({'standard_name': v[3]} if v[3] else {}),
                    aeronet_name=k) for k, v in {**SPECTRAL, **SCALAR}.items()}
ATTRS['instrument'].pop('units')
ATTRS['wl_exact'] = dict(units='nm', long_name='exact central wavelength of the band')
for _rrs, _lwn in RRS_FROM.items():
    ATTRS[_rrs] = dict(units='sr-1', long_name=f'remote sensing reflectance from {_lwn}',
                       standard_name='surface_ratio_of_upwelling_radiance_emerging_from_sea_water_'
                                     'to_downwelling_radiative_flux_in_air',
                       comment=f'{_lwn} / F0, F0 of {F0_SOURCE} averaged over a {FWHM:g} nm FWHM Gaussian '
                               'band centred on wl_exact')
ATTRS['sza'] = dict(units='degree', long_name='solar zenith angle (mean over the bands)',
                    standard_name='solar_zenith_angle')
ATTRS['quality_level'] = dict(long_name='AERONET data quality level')
ATTRS['last_processed'] = dict(long_name='date of the last AERONET processing of the record')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def add_rrs(spectral):
    '''Rrs (sr-1) from the normalized water-leaving radiances (uW cm-2 nm-1 sr-1)'''
    F0 = f0(spectral['wl_exact'])                    # mW m-2 nm-1 = 0.1 uW cm-2 nm-1
    for rrs, lwn in RRS_FROM.items():
        if lwn in spectral:
            spectral[rrs] = 10. * spectral[lwn] / F0
    return spectral


def _f32(values):
    '''float32 copy; the values that are not finite or out of the float32 range (e.g. the
    occasional diverging AERONET chlorophyll retrievals) are set to NaN'''
    values = np.asarray(values, dtype=float)
    with np.errstate(invalid='ignore'):
        bad = ~np.isfinite(values) | (np.abs(values) > np.finfo('float32').max)
    return np.where(bad, np.nan, values).astype('float32')


def to_dataset(rec, level):
    '''CF/ACDD dataset of the records of a site'''
    spectral = add_rrs(dict(rec.spectral))
    order = np.argsort(rec.time.values, kind='stable')
    ds = xr.Dataset(coords=dict(time=('time', rec.time.values[order]), wl=('wl', rec.wl)))
    for name, values in spectral.items():
        ds[name] = (('time', 'wl'), _f32(values[order]), ATTRS.get(name, {}))
    with np.errstate(all='ignore'):
        sza = np.nanmean(spectral['sza_band'], axis=1) if 'sza_band' in spectral else None
    if sza is not None:
        ds['sza'] = ('time', _f32(sza[order]), ATTRS['sza'])
    for name in rec.scalar:
        values = rec.scalar[name].to_numpy()[order]
        if name == 'instrument':
            values = np.nan_to_num(values, nan=-1).astype('int32')
        elif values.dtype.kind == 'f':
            values = _f32(values)
        elif values.dtype.kind == 'O':
            values = values.astype(str)
        ds[name] = ('time', values, ATTRS.get(name, {}))

    s = rec.site
    ds['station_name'] = ((), s['name'], dict(cf_role='timeseries_id', long_name='AERONET site name'))
    ds = ds.assign_coords(
        latitude=((), s.get('latitude', np.nan), dict(units='degrees_north', standard_name='latitude')),
        longitude=((), s.get('longitude', np.nan), dict(units='degrees_east', standard_name='longitude')),
        elevation=((), s.get('elevation', np.nan), dict(units='m', standard_name='height_above_mean_sea_level',
                                                         positive='up')))
    ds.time.attrs.update(standard_name='time', long_name='time of the measurement (UTC)', axis='T')
    ds.wl.attrs.update(units='nm', long_name='nominal wavelength', standard_name='radiation_wavelength')
    now = pd.Timestamp.now("UTC").strftime('%Y-%m-%dT%H:%M:%SZ')
    ds.attrs = {
        'Conventions': 'CF-1.10, ACDD-1.3',
        'featureType': 'timeSeries',
        'title': f'AERONET-OC version 3 {level} data of {s["name"]}',
        'summary': 'SeaPRISM above-water radiometry of the AERONET-OC network (all points), reformatted '
                   'from the AERONET web service, with the remote sensing reflectances Rrs_* computed '
                   'from the normalized water-leaving radiances.',
        'source': 'AERONET-OC version 3 (https://aeronet.gsfc.nasa.gov), ' + s.get('aeronet_product', level),
        'references': 'Zibordi et al. (2009), J. Atmos. Oceanic Technol. 26, 1634-1651; '
                      'Zibordi et al. (2021), J. Atmos. Oceanic Technol. 38, 725-746',
        'institution': 'NASA GSFC AERONET; site PI: ' + s.get('pi_name', 'unknown'),
        'aeronet_site': s['name'],
        'aeronet_level': level,
        'site_pi': s.get('pi_name', ''),
        'site_pi_email': s.get('pi_email', ''),
        'geospatial_lat_min': s.get('latitude', np.nan), 'geospatial_lat_max': s.get('latitude', np.nan),
        'geospatial_lon_min': s.get('longitude', np.nan), 'geospatial_lon_max': s.get('longitude', np.nan),
        'time_coverage_start': f'{pd.Timestamp(ds.time.values[0]):%Y-%m-%dT%H:%M:%SZ}',
        'time_coverage_end': f'{pd.Timestamp(ds.time.values[-1]):%Y-%m-%dT%H:%M:%SZ}',
        'date_created': now,
        'history': f'{now} created by aeronet_oc_db {__version__}',
        'license': 'AERONET data policy: acknowledge AERONET and the site PI, see '
                   'https://aeronet.gsfc.nasa.gov/new_web/data_usage.html',
    }
    return ds


def write_netcdf(ds, path):
    enc = {}
    for name, v in ds.data_vars.items():
        if v.dtype.kind in 'fi' and v.ndim:
            enc[name] = dict(zlib=True, complevel=4, shuffle=True)
            if v.dtype.kind == 'f':
                enc[name]['_FillValue'] = np.float32(np.nan)
    enc['time'] = dict(units='seconds since 1970-01-01 00:00:00', calendar='standard', dtype='int64')
    if 'last_processed' in ds:
        enc['last_processed'] = dict(units='days since 1970-01-01', dtype='float64')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    ds.to_netcdf(tmp, engine='netcdf4', encoding=enc)
    os.replace(tmp, path)


def to_table(ds):
    '''long table (one row per record and band) of a dataset'''
    nt, nw = ds.sizes['time'], ds.sizes['wl']
    cols = {'time': np.repeat(ds.time.values, nw), 'wl': np.tile(ds.wl.values.astype('float32'), nt)}
    for name, v in ds.data_vars.items():
        if v.dims == ('time', 'wl'):
            cols[name] = v.values.ravel()
        elif v.dims == ('time',):
            cols[name] = np.repeat(v.values, nw)
    table = pa.table(cols)
    meta = {k: str(v) for k, v in ds.attrs.items()}
    meta.update(latitude=str(float(ds.latitude)), longitude=str(float(ds.longitude)))
    return table.replace_schema_metadata({**{k: v for k, v in meta.items()}})


def write_parquet(ds, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    pq.write_table(to_table(ds), tmp, compression='zstd', compression_level=6, row_group_size=200_000)
    os.replace(tmp, path)


def build_site(raw_path, nc_path, pq_path, level):
    '''Convert a raw file; returns the catalog row (or ``None`` if the file holds no data, in which
    case stale products are removed).'''
    from .raw import RawFile
    rec = parse(RawFile.read(raw_path))
    if rec is None:
        for p in (nc_path, pq_path):
            if os.path.exists(p):
                os.remove(p)
        return None
    ds = to_dataset(rec, level)
    write_netcdf(ds, nc_path)
    write_parquet(ds, pq_path)
    lwn = ds['Lwn_fQ'] if 'Lwn_fQ' in ds else ds['Lwn']
    valid_wl = ds.wl.values[lwn.notnull().any('time').values]
    return dict(n_records=ds.sizes['time'],
                start=pd.Timestamp(ds.time.values[0]), end=pd.Timestamp(ds.time.values[-1]),
                latitude=float(ds.latitude), longitude=float(ds.longitude), elevation=float(ds.elevation),
                wavelengths=' '.join(f'{w:g}' for w in valid_wl),
                instruments=' '.join(str(i) for i in np.unique(ds.instrument.values) if i >= 0)
                if 'instrument' in ds else '',
                pi_name=ds.attrs['site_pi'])
