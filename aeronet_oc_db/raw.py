'''
Raw AERONET-OC version 3 files (``<site>_OCv3.lev15``, ``.lev20``): parsing into tidy tables.

The raw files keep the layout written by the legacy shell tool (HTML page of the AERONET web
service with the tags stripped line by line), so that the existing readers (``radcalnet_oc``,
``grstbx``, ``hgrs``: header on line 8, data from line 9) keep working.
'''
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ENCODING = 'latin_1'
HEADER_START = 'AERONET_Site,'
NA_VALUES = ['N/A', '-999', '-999.', '-999.0', '-999.000000', '-9.999999']

# AERONET spectral field -> (name, units, long name, standard name)
SPECTRAL = {
    'Solar_Zenith_Angle': ('sza_band', 'degree', 'solar zenith angle at the time of the band measurement',
                           'solar_zenith_angle'),
    'Solar_Azimuth_Angle': ('saa_band', 'degree', 'solar azimuth angle at the time of the band measurement',
                            'solar_azimuth_angle'),
    'Lt_mean': ('Lt', 'uW cm-2 nm-1 sr-1', 'total radiance above the sea surface (mean)', None),
    'Lt_stddev': ('Lt_std', 'uW cm-2 nm-1 sr-1', 'standard deviation of the total radiance', None),
    'Lt_min_rel': ('Lt_min_rel', '1', 'relative minimum of the total radiance', None),
    'Li_mean': ('Li', 'uW cm-2 nm-1 sr-1', 'sky radiance (mean)', None),
    'Li_stddev': ('Li_std', 'uW cm-2 nm-1 sr-1', 'standard deviation of the sky radiance', None),
    'Rho': ('rho', '1', 'sea surface reflectance factor of the sky radiance', None),
    'Aerosol_Optical_Depth': ('aot', '1', 'aerosol optical thickness',
                              'atmosphere_optical_thickness_due_to_ambient_aerosol_particles'),
    'Ozone_Optical_Depth': ('tau_o3', '1', 'ozone optical thickness', None),
    'Rayleigh_Optical_Depth': ('tau_rayleigh', '1', 'Rayleigh optical thickness', None),
    'NO2_Optical_Depth': ('tau_no2', '1', 'NO2 optical thickness', None),
    'Water_Vapor_Optical_Depth': ('tau_h2o', '1', 'water vapour optical thickness', None),
    'Lw': ('Lw', 'uW cm-2 nm-1 sr-1', 'water-leaving radiance', None),
    'LwQ': ('LwQ', 'uW cm-2 nm-1 sr-1', 'water-leaving radiance corrected for the Q factor', None),
    'Lwn': ('Lwn', 'uW cm-2 nm-1 sr-1', 'normalized water-leaving radiance', None),
    'Lwn_f/Q': ('Lwn_fQ', 'uW cm-2 nm-1 sr-1',
                'normalized water-leaving radiance corrected for the bidirectional effects (f/Q, Morel 2002)',
                None),
    'Lwn_IOP': ('Lwn_IOP', 'uW cm-2 nm-1 sr-1',
                'normalized water-leaving radiance corrected for the bidirectional effects (IOP based)', None),
}

# AERONET scalar field -> (name, units, long name, standard name)
SCALAR = {
    'Total_Ozone(Du)': ('tco3', 'DU', 'total column ozone', None),
    'Total_NO2(DU)': ('tno2', 'DU', 'total column NO2', None),
    'Total_Precipitable_Water(cm)': ('tcwv', 'cm', 'total precipitable water', None),
    'Chlorophyll-a': ('chl', 'mg m-3', 'chlorophyll-a concentration (AERONET-OC standard algorithm)',
                      'mass_concentration_of_chlorophyll_a_in_sea_water'),
    'Wind_Speed(m/s)': ('wind', 'm s-1', 'wind speed', 'wind_speed'),
    'Pressure(hPa)': ('pressure', 'hPa', 'surface pressure', 'surface_air_pressure'),
    'ASea(Degrees)': ('raa_sea', 'degree', 'relative azimuth of the sea radiance measurements', None),
    'ZSea(Degrees)': ('vza_sea', 'degree', 'viewing zenith angle of the sea radiance measurements', None),
    'AERONET_Instrument_Number': ('instrument', '1', 'AERONET instrument number', None),
}
TEXT = {'Data_Quality_Level': 'quality_level', 'Last_Date_Processed': 'last_processed'}
SITE_FIELDS = {'Site_Latitude(Degrees)': 'latitude', 'Site_Longitude(Degrees)': 'longitude',
               'Site_Elevation(m)': 'elevation'}


@dataclass
class RawFile:
    '''Content of a raw AERONET-OC file.

    :ivar preamble: lines before the column header (HTML remains, data level, PI contact)
    :ivar header: column names
    :ivar rows: data lines (without end of line), in file order
    '''
    preamble: list
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    trailer: str = '\n'

    @classmethod
    def from_text(cls, text):
        body = text.rstrip('\n')
        trailer = text[len(body):] or '\n'      # blank lines at the end (kept for a byte-identical copy)
        lines = body.split('\n')
        for i, line in enumerate(lines):
            if line.startswith(HEADER_START):
                return cls(lines[:i], line.split(','), [r for r in lines[i + 1:] if r.strip()], trailer)
        return cls(lines, trailer=trailer)       # no data for the site and period

    @classmethod
    def read(cls, path):
        with open(path, encoding=ENCODING) as f:
            return cls.from_text(f.read())

    def to_text(self):
        lines = list(self.preamble)
        if self.header:
            lines += [','.join(self.header)] + self.rows
        return '\n'.join(lines) + self.trailer

    @property
    def empty(self):
        return not self.rows

    @property
    def meta(self):
        '''data level and PI as given in the preamble'''
        out = {}
        for line in self.preamble:
            m = re.match(r'\s*Version 3: (.*?),?\s*$', line)
            if m:
                out['aeronet_product'] = m.group(1)
            m = re.match(r'\s*Contact: PI=(.*?); PI Email=(.*?)\s*$', line)
            if m:
                out['pi_name'], out['pi_email'] = m.group(1).replace('_', ' '), m.group(2)
        return out

    @staticmethod
    def row_time(row):
        '''time of a data line (UTC)'''
        _, date, time = row.split(',', 3)[:3]
        return pd.to_datetime(date + ' ' + time, format='%d:%m:%Y %H:%M:%S')

    def merge(self, newer, since):
        '''Records of ``self`` before ``since`` followed by the records of ``newer`` (downloaded
        from ``since`` onwards). The preamble and header of ``newer`` are kept.'''
        if newer.empty:
            return RawFile(self.preamble, self.header, [r for r in self.rows if self.row_time(r) < since], self.trailer)
        if self.header and self.header != newer.header:
            raise ValueError('the columns of the AERONET-OC files changed: full download required')
        old = [r for r in self.rows if self.row_time(r) < since]
        return RawFile(newer.preamble, newer.header, old + newer.rows, newer.trailer)


@dataclass
class Records:
    '''Records of a raw file on a (time, wl) grid.

    :ivar time: times of the records (UTC), file order
    :ivar wl: nominal wavelengths (nm) measured at least once
    :ivar spectral: name -> array (time, wl), including ``wl_exact`` (nm)
    :ivar scalar: DataFrame of the scalar fields (one row per record)
    :ivar site: name, latitude, longitude, elevation and preamble metadata
    '''
    time: pd.DatetimeIndex
    wl: np.ndarray
    spectral: dict
    scalar: pd.DataFrame
    site: dict


def parse(raw):
    '''Records of a raw file (``None`` if it holds no data).'''
    if raw.empty:
        return None
    from io import StringIO
    header = raw.header
    spec_cols, exact_cols, named = {}, {}, {}
    for j, name in enumerate(header):
        m = re.match(r'(.+)\[(\d+)nm\]$', name)
        if m and m.group(1) in SPECTRAL:
            spec_cols.setdefault(m.group(1), {})[float(m.group(2))] = j
        m = re.match(r'Exact_Wavelengths\(um\)_(\d+)$', name)
        if m:
            exact_cols[float(m.group(1))] = j
        if name in SCALAR or name in TEXT or name in SITE_FIELDS:
            named.setdefault(name, j)
    text_cols = [0, 1, 2] + [named[n] for n in TEXT if n in named]
    usecols = sorted(set(text_cols + list(exact_cols.values()) + list(named.values())
                         + [j for cols in spec_cols.values() for j in cols.values()]))
    df = pd.read_csv(StringIO('\n'.join(raw.rows)), header=None, names=range(len(header)), usecols=usecols,
                     na_values=NA_VALUES, keep_default_na=False, dtype={j: str for j in text_cols},
                     low_memory=False)
    num = lambda j: pd.to_numeric(df[j], errors='coerce').to_numpy(dtype=float)
    time = pd.DatetimeIndex(pd.to_datetime(df[1] + ' ' + df[2], format='%d:%m:%Y %H:%M:%S'), name='time')

    exact = {w: num(j) * 1000. for w, j in exact_cols.items()}
    wls = np.array([w for w, v in exact.items() if np.isfinite(v).any()])
    spectral = {'wl_exact': np.stack([exact[w] for w in wls], axis=1)}
    for aname, cols in spec_cols.items():
        values = np.full((len(df), len(wls)), np.nan)
        for k, w in enumerate(wls):
            if w in cols:
                values[:, k] = num(cols[w])
        spectral[SPECTRAL[aname][0]] = values

    scalar = pd.DataFrame(index=time)
    for aname, (name, *_rest) in SCALAR.items():
        if aname in named:
            scalar[name] = num(named[aname])
    for aname, name in TEXT.items():
        if aname in named:
            scalar[name] = df[named[aname]].to_numpy()
    if 'last_processed' in scalar:
        scalar['last_processed'] = pd.to_datetime(scalar['last_processed'], format='%d:%m:%Y', errors='coerce')

    site = dict(raw.meta, name=df[0].iloc[0])
    for aname, name in SITE_FIELDS.items():
        if aname in named:
            site[name] = float(np.nanmedian(num(named[aname])))
    return Records(time, wls, spectral, scalar, site)
