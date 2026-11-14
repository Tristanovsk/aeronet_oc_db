'''
AERONET-OC database: update of the raw files, build of the products, catalog and readers.

Layout of the root folder (see :mod:`aeronet_oc_db.config`: ``$AERONET_OC_ROOT``, the user config file or
``/DATA/AERONET/OCv3``)::

    <site>_OCv3.lev15, <site>_OCv3.lev20      raw files (legacy layout, read by the existing tools)
    db/catalog.csv                             one row per site and level (coverage, location, checksums)
    db/netcdf/<level>/<site>_OCv3.<level>.nc   CF/ACDD netCDF-4, dims (time, wl)
    db/parquet/level=<level>/site=<site>/data.parquet   Hive-partitioned Parquet, one row per (record, band)
    db/update.log                              log of the updates
'''
import glob
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from . import download
from .build import build_site, sha256
from .config import default_root
from .raw import RawFile
from .sites import s2_tile

DEFAULT_ROOT = default_root()
LEVELS = ('lev15', 'lev20')
CATALOG_COLUMNS = ['site', 'level', 'n_records', 'start', 'end', 'latitude', 'longitude', 'elevation',
                   's2_tile',                    'wavelengths', 'instruments', 'pi_name', 'raw_file', 'raw_sha256', 'raw_updated',
                   'netcdf', 'parquet', 'built']
log = logging.getLogger('aeronet_oc_db')


class Database:
    '''AERONET-OC version 3 database.

    Example::

        from aeronet_oc_db import Database
        db = Database()                          # location: see aeronet_oc_db.config
        db.update(levels=('lev15', 'lev20'))     # download, then build the changed sites
        ds = db.open('AAOT', 'lev20')            # xarray.Dataset (time, wl)
        df = db.table('lev20', sites=['AAOT', 'Venise'], start='2023-01-01',
                      columns=['Rrs_Lwn_fQ', 'aot', 'sza'])
        mu = db.match('AAOT', ['2023-07-01T10:05'], max_lag='1h')   # in situ at the overpass

    :param root: folder of the raw files (the products are written in ``root/db``)
    '''

    def __init__(self, root=None):
        self.root = Path(root).expanduser() if root else default_root()
        self.dir = self.root / 'db'
        self.catalog_file = self.dir / 'catalog.csv'

    # ------------------------------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------------------------------
    def raw_path(self, site, level):
        return self.root / f'{site}_OCv3.{level}'

    def netcdf_path(self, site, level):
        return self.dir / 'netcdf' / level / f'{site}_OCv3.{level}.nc'

    def parquet_path(self, site, level):
        return self.dir / 'parquet' / f'level={level}' / f'site={site}' / 'data.parquet'

    def raw_sites(self):
        '''sites of the raw files'''
        names = (re.match(r'(.*)_OCv3\.lev\d\d$', os.path.basename(f)) for f in glob.glob(str(self.root / '*_OCv3.lev*')))
        return sorted({m.group(1) for m in names if m}, key=str.lower)

    # ------------------------------------------------------------------------------------------
    # catalog
    # ------------------------------------------------------------------------------------------
    @property
    def catalog(self):
        '''one row per site and level (only the sites with data)'''
        if not self.catalog_file.exists():
            return pd.DataFrame(columns=CATALOG_COLUMNS)
        cat = pd.read_csv(self.catalog_file, parse_dates=['start', 'end', 'raw_updated', 'built'],
                          dtype={'wavelengths': str, 'instruments': str, 's2_tile': str})
        if 's2_tile' not in cat:            # catalog written by version 1.0
            cat.insert(cat.columns.get_loc('elevation') + 1, 's2_tile',
                       [s2_tile(*v) for v in zip(cat.site, cat.longitude, cat.latitude)])
        return cat

    def site_info(self, level=None):
        '''One row per site (index ``site``): latitude, longitude, elevation, Sentinel-2 tile and, for each
        level, the number of records and the first and last dates.

        With ``level``, the columns of the former ``info_aeronet_oc_v15_location.csv`` table
        (``start_date``, ``end_date``, ``number``, ``latitude``, ``longitude``, ``s2_tile``) for that level,
        all the sites included (``number`` = 0 without data).'''
        cat = self.catalog
        if cat.empty:
            return pd.DataFrame()
        if level is not None:
            info = self.site_info()
            n = info.get(f'n_{level}', pd.Series(0, index=info.index))
            fmt = lambda c: info[c].dt.strftime('%Y-%m-%d') if c in info else ''
            return pd.DataFrame({'start_date': fmt(f'start_{level}'), 'end_date': fmt(f'end_{level}'),
                                 'number': n, 'latitude': info.latitude, 'longitude': info.longitude,
                                 's2_tile': info.s2_tile}, index=info.index)
        info = cat.groupby('site', sort=False)[['latitude', 'longitude', 'elevation', 's2_tile']].first()
        for level, g in cat.groupby('level'):
            g = g.set_index('site')
            info[f'n_{level}'] = g.n_records
            info[f'start_{level}'] = g.start
            info[f'end_{level}'] = g.end
        for c in info.filter(like='n_lev'):
            info[c] = info[c].fillna(0).astype(int)
        return info.sort_index(key=lambda s: s.str.lower())

    def _save_catalog(self, cat):
        os.makedirs(self.dir, exist_ok=True)
        cat = cat.copy()
        cat['s2_tile'] = [s2_tile(*v) for v in zip(cat.site, cat.longitude, cat.latitude)]
        cat = cat[CATALOG_COLUMNS].sort_values(['site', 'level'], key=lambda s: s.str.lower())
        tmp = str(self.catalog_file) + '.tmp'
        cat.to_csv(tmp, index=False, date_format='%Y-%m-%d %H:%M:%S')
        os.replace(tmp, self.catalog_file)

    def sites(self, level='lev20'):
        '''sites with data at a level'''
        cat = self.catalog
        return cat.loc[cat.level == level, 'site'].tolist()

    # ------------------------------------------------------------------------------------------
    # update
    # ------------------------------------------------------------------------------------------
    def download(self, sites=None, levels=LEVELS, since=None, discover=False, workers=2):
        '''Download the data and update the raw files whose records changed.

        :param sites: sites to update (default: the sites of the raw files, plus the new active
            sites if ``discover``)
        :param levels: data levels
        :param since: if given, only the records from this date are downloaded and merged with the
            raw file (records before ``since`` are kept); else the whole record is downloaded, which
            takes the reprocessings of the past data into account (recommended for ``lev20``)
        :param discover: add the sites with data within the last 60 days (one request)
        :param workers: parallel requests
        :return: DataFrame (site, level, status, n_records)
        '''
        sites = list(sites) if sites else self.raw_sites()
        if discover:
            new = [s for s in download.active_sites(days=60) if s not in sites]
            if new:
                log.info('new active sites: %s', ', '.join(new))
            sites += new
        since = pd.Timestamp(since) if since is not None else None
        jobs = [(s, lev) for s in sites for lev in levels]

        def job(args):
            site, level = args
            path = self.raw_path(site, level)
            try:
                if since is not None and path.exists():
                    new = RawFile.read(path).merge(download.fetch(site, level, start=since), since)
                else:
                    new = download.fetch(site, level)
            except Exception as e:      # keep the existing file
                log.error('%s %s: %s', site, level, e)
                return dict(site=site, level=level, status=f'error: {e}', n_records=np.nan)
            old = RawFile.read(path) if path.exists() else None
            if old is not None and old.rows == new.rows and old.header == new.header:
                status = 'unchanged'
            else:
                tmp = str(path) + '.tmp'
                with open(tmp, 'w', encoding='latin_1', newline='\n') as f:
                    f.write(new.to_text())
                os.replace(tmp, path)
                delta = 0 if old is None else len(new.rows) - len(old.rows)
                status = 'new' if old is None else (f'updated ({delta:+d} records)' if delta else 'updated (reprocessed)')
            log.info('%s %s: %s', site, level, status)
            return dict(site=site, level=level, status=status, n_records=len(new.rows))

        with ThreadPoolExecutor(max_workers=workers) as ex:
            return pd.DataFrame(list(ex.map(job, jobs)))

    def build(self, sites=None, levels=LEVELS, force=False):
        '''Convert the raw files whose checksum changed since the last build (or all if ``force``)
        into netCDF and Parquet, and update the catalog.

        :return: DataFrame (site, level, status)
        '''
        cat = self.catalog
        known = {(r.site, r.level): r for r in cat.itertuples()}
        rows = {k: r._asdict() for k, r in known.items()}
        report = []
        sites = list(sites) if sites else self.raw_sites()
        for site in sites:
            for level in levels:
                raw = self.raw_path(site, level)
                if not raw.exists():
                    continue
                nc, pqf = self.netcdf_path(site, level), self.parquet_path(site, level)
                digest = sha256(raw)
                prev = known.get((site, level))
                if (not force and prev is not None and prev.raw_sha256 == digest
                        and nc.exists() and pqf.exists()):
                    report.append(dict(site=site, level=level, status='up to date'))
                    continue
                try:
                    info = build_site(str(raw), str(nc), str(pqf), level)
                except Exception as e:
                    log.exception('%s %s: build failed', site, level)
                    report.append(dict(site=site, level=level, status=f'error: {e}'))
                    continue
                if info is None:
                    rows.pop((site, level), None)
                    report.append(dict(site=site, level=level, status='no data'))
                    continue
                now = pd.Timestamp.now("UTC").tz_localize(None)
                rows[(site, level)] = dict(
                    site=site, level=level, **info, raw_file=raw.name, raw_sha256=digest,
                    raw_updated=pd.Timestamp(os.path.getmtime(raw), unit='s'),
                    netcdf=str(nc.relative_to(self.dir)), parquet=str(pqf.relative_to(self.dir)), built=now)
                report.append(dict(site=site, level=level, status='built', n_records=info['n_records']))
                log.info('%s %s: built (%d records)', site, level, info['n_records'])
                self._save_catalog(pd.DataFrame(list(rows.values())))   # keep the progress
        if rows:
            self._save_catalog(pd.DataFrame(list(rows.values())))
        return pd.DataFrame(report)

    def update(self, sites=None, levels=LEVELS, since=None, discover=False, workers=2, force=False):
        ''':meth:`download` then :meth:`build`; the run is logged in ``db/update.log``'''
        os.makedirs(self.dir, exist_ok=True)
        handler = logging.FileHandler(self.dir / 'update.log')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(handler)
        try:
            log.info('update: levels=%s since=%s discover=%s', levels, since, discover)
            dl = self.download(sites, levels, since, discover, workers)
            built = self.build(sites=dl.site.unique() if sites else None, levels=levels, force=force)
            return dl.merge(built.rename(columns={'status': 'build'})[['site', 'level', 'build']],
                            on=['site', 'level'], how='outer')
        finally:
            log.removeHandler(handler)
            handler.close()

    # ------------------------------------------------------------------------------------------
    # readers
    # ------------------------------------------------------------------------------------------
    def open(self, site, level='lev20', legacy_names=False, **kwargs):
        '''netCDF dataset of a site (lazy, see :func:`xarray.open_dataset`).

        :param legacy_names: variable names of the former ``AERONET/netcdf/OCv3`` files
            (:func:`aeronet_oc_db.legacy.to_legacy`), for the code written for them
        '''
        path = self.netcdf_path(site, level)
        if not path.exists():
            raise FileNotFoundError(f'{path} (no data, or run Database.build)')
        ds = xr.open_dataset(path, **kwargs)
        if legacy_names:
            from .legacy import to_legacy
            ds = to_legacy(ds)
        return ds

    def table(self, level='lev20', sites=None, start=None, end=None, columns=None, wl=None):
        '''Records of several sites as a long DataFrame (one row per record and band), read from the
        Parquet dataset with the filters pushed down.

        :param sites: site names (default: all)
        :param start, end: time limits (UTC, included)
        :param columns: columns to read (``time``, ``wl`` and ``site`` are always returned)
        :param wl: nominal wavelengths to keep
        '''
        import pyarrow.dataset as pds
        dset = pds.dataset(self.dir / 'parquet', format='parquet', partitioning='hive')
        flt = pds.field('level') == level
        if sites is not None:
            flt &= pds.field('site').isin(list(sites))
        if start is not None:
            flt &= pds.field('time') >= pd.Timestamp(start).to_datetime64()
        if end is not None:
            flt &= pds.field('time') <= pd.Timestamp(end).to_datetime64()
        if wl is not None:
            flt &= pds.field('wl').isin([float(w) for w in np.atleast_1d(wl)])
        cols = None if columns is None else ['site', 'time', 'wl'] + [c for c in columns if c not in ('site', 'time', 'wl')]
        df = dset.to_table(filter=flt, columns=cols).to_pandas()
        df['site'] = df['site'].astype(str)
        return df.sort_values(['site', 'time', 'wl'], ignore_index=True)

    def match(self, site, times, level='lev20', max_lag='1h', stat='median'):
        '''In situ data at given times (e.g. satellite overpasses): statistics of the records within
        ``max_lag`` of each time.

        :param times: times (UTC)
        :param stat: ``'median'``, ``'mean'`` or ``'nearest'``
        :return: Dataset with a ``match`` dimension: the time of the request (``time``), the
            statistics of the variables, ``n_records`` (records used: 1 for ``'nearest'``), ``lag_min`` (median lag of the records, or lag of
            the nearest one, in minutes) and ``std`` of ``Rrs_Lwn_fQ``; matches without record are dropped
        '''
        ds = self.open(site, level).load()
        lag = pd.Timedelta(max_lag)
        out = []
        for t in pd.DatetimeIndex(np.atleast_1d(times)):
            sel = ds.sel(time=slice(t - lag, t + lag))
            if sel.sizes['time'] == 0:
                continue
            lags = np.abs((sel.time.values - t.to_datetime64()) / np.timedelta64(1, 'm'))
            num = sel[[v for v in sel.data_vars if sel[v].dtype.kind in 'fi' and 'time' in sel[v].dims]]
            if stat == 'nearest':
                k = int(np.argmin(lags))
                r, lag_min, n = num.isel(time=k), float(lags[k]), 1
            else:
                r = getattr(num, stat)('time', skipna=True, keep_attrs=True)
                lag_min, n = float(np.median(lags)), sel.sizes['time']
            r = r.drop_vars('time', errors='ignore')
            r['n_records'] = n                     # records used
            r['lag_min'] = lag_min
            if 'Rrs_Lwn_fQ' in sel:
                r['Rrs_Lwn_fQ_std'] = sel.Rrs_Lwn_fQ.std('time')
            out.append(r.expand_dims(match=1).assign_coords(time=('match', [t.to_datetime64()])))
        if not out:
            return xr.Dataset()
        res = xr.concat(out, 'match')
        res.attrs.update(ds.attrs, match_stat=stat, match_max_lag=str(lag))
        return res
