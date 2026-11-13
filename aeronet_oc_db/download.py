'''
Download of the AERONET-OC version 3 data from the AERONET web service (``print_web_data_v3``,
all points, ``AVG=10``).
'''
import re
import time as _time

import pandas as pd
import requests

from .raw import RawFile

URL = 'https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3'
FIRST_DATE = pd.Timestamp('1993-01-01')   # earliest date accepted by the web service
LEVELS = ('lev10', 'lev15', 'lev20')
USER_AGENT = 'aeronet_oc_db (AERONET-OC database updater)'


def _params(level, start, end, site=None):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    p = dict(year=start.year, month=start.month, day=start.day,
             year2=end.year, month2=end.month, day2=end.day, AVG=10)
    p['LWN' + level[-2:]] = 1
    if site:
        p['site'] = site
    return p


def _strip_html(text):
    '''same as the legacy ``sed -e 's/<[^>]*>//g'``: tags removed line by line'''
    return '\n'.join(re.sub(r'<[^>]*>', '', line) for line in text.split('\n'))


def _get(params, retries=4, timeout=600):
    err = None
    for k in range(retries):
        try:
            r = requests.get(URL, params=params, timeout=timeout, headers={'User-Agent': USER_AGENT})
            r.raise_for_status()
            r.encoding = 'latin_1'
            text = r.text
            if 'Web Service Help' in text or 'AERONET Data Download' not in text:
                raise ValueError('unexpected answer of the AERONET web service (help or error page)')
            return text
        except (requests.RequestException, ValueError) as e:
            err = e
            _time.sleep(10 * 2 ** k)
    raise RuntimeError(f'download failed ({params}): {err}')


def fetch(site, level='lev20', start=FIRST_DATE, end=None):
    '''Data of a site between two dates (included), in the layout of the legacy raw files.

    :param site: AERONET site name (e.g. ``'AAOT'``)
    :param level: ``'lev10'``, ``'lev15'`` or ``'lev20'``
    :return: :class:`~aeronet_oc_db.raw.RawFile` (empty if the site has no data)
    '''
    if level not in LEVELS:
        raise ValueError(f'level must be one of {LEVELS}')
    end = pd.Timestamp.utcnow().tz_localize(None).normalize() if end is None else end
    return RawFile.from_text(_strip_html(_get(_params(level, start, end, site))))


def active_sites(days=60, level='lev15', end=None):
    '''Sites with data within the last ``days`` (one request for all the sites): used to discover
    the new AERONET-OC sites.

    :return: dict site -> number of records
    '''
    end = pd.Timestamp.utcnow().tz_localize(None).normalize() if end is None else pd.Timestamp(end)
    raw = RawFile.from_text(_strip_html(_get(_params(level, end - pd.Timedelta(days=days), end))))
    return pd.Series([r.split(',', 1)[0] for r in raw.rows]).value_counts().sort_index().to_dict()
