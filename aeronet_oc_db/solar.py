'''
Extraterrestrial solar irradiance used to convert the normalized water-leaving radiances into
remote sensing reflectances.
'''
from functools import lru_cache
from importlib.resources import files

import numpy as np
import pandas as pd

F0_FILE = files('aeronet_oc_db.data').joinpath('Thuillier_2003_0.3nm.dat')
F0_SOURCE = 'Thuillier et al. (2003), Solar Physics 214, 1-22'
FWHM = 10.   # nm, bandwidth of the SeaPRISM / SeaWiFS-like filters


@lru_cache()
def _spectrum():
    s = pd.read_csv(F0_FILE, skiprows=15, sep='\t', header=None, names=('wl', 'F0'))
    return s.wl.to_numpy(), s.F0.to_numpy()


def f0(wl, fwhm=FWHM):
    '''Solar irradiance (mW m-2 nm-1) averaged over a Gaussian band of full width ``fwhm`` (nm)
    centred on each wavelength ``wl`` (nm); NaN for NaN wavelengths.'''
    wl = np.asarray(wl, dtype=float)
    out = np.full(wl.shape, np.nan)
    ok = np.isfinite(wl)
    if ok.any():
        uniq, inv = np.unique(np.round(wl[ok], 2), return_inverse=True)
        sw, sf = _spectrum()
        w = np.exp(-0.5 * ((sw[None, :] - uniq[:, None]) / (fwhm / 2.3548)) ** 2)
        out[ok] = ((w * sf).sum(1) / w.sum(1))[inv]
    return out
