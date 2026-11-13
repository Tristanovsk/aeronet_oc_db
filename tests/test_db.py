import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from aeronet_oc_db import Database, RawFile, parse
from aeronet_oc_db.solar import f0

DATA = Path(__file__).parent / 'data'
BLYTH = DATA / 'Blyth_NOAH_OCv3.lev15'


@pytest.fixture
def db(tmp_path):
    for f in DATA.glob('*_OCv3.lev*'):
        shutil.copy(f, tmp_path)
    return Database(tmp_path)


def test_raw_roundtrip_keeps_legacy_layout():
    raw = RawFile.read(BLYTH)
    assert raw.to_text() == BLYTH.read_text(encoding='latin_1')
    lines = BLYTH.read_text(encoding='latin_1').splitlines()
    assert lines[7].startswith('AERONET_Site,')          # header on line 8 (legacy readers: skiprows=8)
    assert raw.meta['pi_name'] == 'Rodney Forster'


def test_parse():
    rec = parse(RawFile.read(BLYTH))
    assert len(rec.time) == 33 and rec.time.is_monotonic_increasing
    assert list(rec.wl) == [412, 443, 490, 532, 551, 667, 870, 1020]
    np.testing.assert_allclose(rec.spectral['wl_exact'][0, 0], 413.0)
    assert np.isclose(rec.spectral['Lwn_fQ'][0, 0], 0.435526)
    assert np.isclose(rec.spectral['Lwn_IOP'][0, 0], 0.428714)
    assert rec.scalar.instrument.iloc[0] == 952
    assert rec.site['latitude'] == pytest.approx(55.146399)


def test_empty_file():
    raw = RawFile.read(DATA / 'Abu_Al_Bukhoosh_OCv3.lev20')
    assert raw.empty and parse(raw) is None


def test_f0_band_average():
    # Thuillier (2003) 10 nm band average at 443 nm, ~1.87-1.90 W m-2 nm-1
    assert 1800 < f0([443.])[0] < 1950
    assert np.isnan(f0([np.nan]))[0]


def test_build_and_readers(db):
    rep = db.build()
    assert set(rep.status) == {'built', 'no data'}
    cat = db.catalog
    assert list(cat.site) == ['Blyth_NOAH']
    ds = db.open('Blyth_NOAH', 'lev15')
    assert ds.attrs['Conventions'].startswith('CF-')
    # Rrs = 10 Lwn / F0
    exp = 10 * ds.Lwn_fQ / f0(ds.wl_exact.values)
    np.testing.assert_allclose(ds.Rrs_Lwn_fQ, exp, rtol=1e-5)
    df = db.table('lev15', sites=['Blyth_NOAH'], start='2016-04-22', end='2016-04-30', wl=[443],
                  columns=['Rrs_Lwn_fQ'])
    assert (df.wl == 443).all() and df.time.min() >= pd.Timestamp('2016-04-22')
    m = db.match('Blyth_NOAH', ['2016-04-21T10:30', '2000-01-01'], level='lev15', max_lag='1h')
    assert m.sizes['match'] == 1 and int(m.n_records[0]) == 1
    # second build: nothing to do
    assert set(db.build().status) <= {'up to date', 'no data'}


def test_merge_incremental():
    raw = RawFile.read(BLYTH)
    since = raw.row_time(raw.rows[20])
    newer = RawFile(raw.preamble, raw.header, raw.rows[20:] + [raw.rows[-1]])
    merged = raw.merge(newer, since)
    assert merged.rows[:20] == raw.rows[:20] and len(merged.rows) == len(raw.rows) + 1


def test_config_resolution(tmp_path, monkeypatch):
    from aeronet_oc_db.config import default_root, FALLBACK_ROOT
    monkeypatch.delenv('AERONET_OC_ROOT', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'cfg'))
    assert default_root() == FALLBACK_ROOT
    (tmp_path / 'cfg' / 'aeronet_oc_db').mkdir(parents=True)
    (tmp_path / 'cfg' / 'aeronet_oc_db' / 'config.toml').write_text(f'root = "{tmp_path}/from_file"\n')
    assert default_root() == tmp_path / 'from_file'
    monkeypatch.setenv('AERONET_OC_ROOT', str(tmp_path / 'from_env'))
    assert default_root() == tmp_path / 'from_env'
    assert Database().root == tmp_path / 'from_env'


def test_mgrs_tile():
    from aeronet_oc_db.sites import mgrs_tile, s2_tile
    assert mgrs_tile(12.5083, 45.3139) == '33TUL'          # Venise, east of the 32/33 zone limit
    assert s2_tile('Venise', 12.5083, 45.3139) == '32TQR'  # curated: tile of the overlap used for Venise
    assert mgrs_tile(-61.722, -39.148) == '20HPB'          # Bahia_Blanca, southern hemisphere


def test_site_info(db):
    db.build()
    info = db.site_info()
    assert info.loc['Blyth_NOAH', 's2_tile'] == '30UWG' and info.loc['Blyth_NOAH', 'n_lev15'] == 33


def test_legacy_names(db):
    db.build()
    ds = db.open('Blyth_NOAH', 'lev15', legacy_names=True)
    for v in ['Aerosol_Optical_Depth', 'Lwn_fQ', 'Rrs_Lwn', 'Rrs_Lwn_fQ', 'Rrs_Lw', 'Total_Precipitable_Water(cm)',
              'Wind_Speed(ms)', 'Pressure(hPa)', 'Chlorophyll-a', 'Solar_Zenith_Angle', 'SZA', 'wavelength',
              'Site_Latitude(Degrees)', 'AERONET_Site_Name', 'year', 'season', 'Day_of_Year(Fraction)',
              'Number_of_Wavelengths']:
        assert v in ds, v
    assert float(ds.wavelength[0, 0]) == pytest.approx(0.413)
    assert float(ds['Site_Latitude(Degrees)'][0]) == pytest.approx(55.146399)
    info = db.site_info('lev15')
    assert list(info.columns) == ['start_date', 'end_date', 'number', 'latitude', 'longitude', 's2_tile']
    assert info.loc['Blyth_NOAH', 'number'] == 33 and info.loc['Blyth_NOAH', 'start_date'] == '2016-04-21'
