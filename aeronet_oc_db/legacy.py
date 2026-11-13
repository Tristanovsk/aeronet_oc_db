'''
Compatibility with the former netCDF conversion of the AERONET-OC files (``AERONET/netcdf/OCv3``, made by
the ``aeronet_oc_datacube_maker`` notebook): variable names of the AERONET files (with ``/`` removed),
``wavelength`` in micrometers, ``SZA``, ``year``, ``season``.

New code should use the database names; this view lets the existing notebooks and scripts switch to the
database by changing only the line that opens the data::

    aeronet_oc = aeronet_oc_db.open_site(site, level, legacy_names=True).load()
'''
import numpy as np

from .build import RRS_FROM
from .raw import SCALAR, SITE_FIELDS, SPECTRAL, TEXT
from .solar import f0


def _legacy(aeronet_name):
    return aeronet_name.replace('/', '')


def to_legacy(ds):
    '''dataset of the database -> variable names of the former ``AERONET/netcdf/OCv3`` files'''
    names = {v[0]: _legacy(k) for k, v in {**SPECTRAL, **SCALAR}.items()}
    names.update({v: k for k, v in TEXT.items()})
    names['sza_band'], names['saa_band'] = 'Solar_Zenith_Angle', 'Solar_Azimuth_Angle'
    names['sza'] = 'SZA'
    out = ds.rename({k: v for k, v in names.items() if k in ds.variables})
    out['wavelength'] = ds.wl_exact / 1000.
    out['wavelength'].attrs = dict(units='um', long_name='exact wavelength of the band')
    out = out.drop_vars('wl_exact')
    if 'Lw' in out:                                       # former product, kept for compatibility
        out['Rrs_Lw'] = 10. * out.Lw / f0(ds.wl_exact.values)
    site = str(ds.station_name.values)
    out['AERONET_Site_Name'] = ('time', np.full(ds.sizes['time'], site))
    out['site'] = out['AERONET_Site_Name']
    for aname, name in SITE_FIELDS.items():
        out[_legacy(aname)] = ('time', np.full(ds.sizes['time'], float(ds[name])))
    doy = ds['time.dayofyear']
    out['Day_of_Year'] = doy
    out['Day_of_Year(Fraction)'] = doy + (ds.time - ds.time.dt.floor('D')) / np.timedelta64(1, 'D')
    out['Number_of_Wavelengths'] = ds.wl_exact.notnull().sum('wl')
    out['year'] = out['time.year']
    out['season'] = out['time.season']
    out.attrs['comment'] = ('legacy variable names (aeronet_oc_db.legacy.to_legacy); Rrs_* = Lwn_* / F0 with F0 '
                            'averaged over 10 nm at the exact wavelength')
    return out
