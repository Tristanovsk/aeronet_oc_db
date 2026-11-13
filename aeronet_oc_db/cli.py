'''
Command line interface::

    aeronet-oc-db update  [--root DIR] [--levels lev15 lev20] [--sites S ...] [--since DATE | --days N]
                          [--discover] [--workers 2] [--force]
    aeronet-oc-db build   [--root DIR] [--levels ...] [--sites ...] [--force]
    aeronet-oc-db discover [--days 60]
    aeronet-oc-db status  [--root DIR]
'''
import argparse
import logging
import sys

import pandas as pd

from .database import DEFAULT_ROOT, LEVELS, Database


def main(argv=None):
    p = argparse.ArgumentParser(prog='aeronet-oc-db', description='AERONET-OC version 3 database')
    sub = p.add_subparsers(dest='cmd', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--root', default=str(DEFAULT_ROOT), help='folder of the raw files (default %(default)s)')
    common.add_argument('--levels', nargs='+', default=list(LEVELS), choices=['lev10', 'lev15', 'lev20'])
    common.add_argument('--sites', nargs='+', help='site names (default: all the sites of the raw files)')
    common.add_argument('--force', action='store_true', help='rebuild the products even if the raw file is unchanged')
    u = sub.add_parser('update', parents=[common], help='download the new data, then build the changed sites')
    g = u.add_mutually_exclusive_group()
    g.add_argument('--since', help='only download the records from this date (merged with the raw file)')
    g.add_argument('--days', type=int, help='only download the records of the last DAYS days')
    u.add_argument('--discover', action='store_true', help='add the new sites with data in the last 60 days')
    u.add_argument('--workers', type=int, default=2, help='parallel requests (default %(default)s)')
    sub.add_parser('build', parents=[common], help='convert the raw files into netCDF / Parquet')
    d = sub.add_parser('discover', help='sites with data within the last days, flagging the unknown ones')
    d.add_argument('--root', default=str(DEFAULT_ROOT))
    d.add_argument('--days', type=int, default=60)
    s = sub.add_parser('status', help='print the catalog')
    s.add_argument('--root', default=str(DEFAULT_ROOT))
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    db = Database(a.root)
    pd.set_option('display.width', 200, 'display.max_rows', 500, 'display.max_columns', 20)
    if a.cmd == 'update':
        since = a.since
        if a.days:
            since = pd.Timestamp.utcnow().tz_localize(None).normalize() - pd.Timedelta(days=a.days)
        res = db.update(a.sites, a.levels, since, a.discover, a.workers, a.force)
        print(res.to_string(index=False))
    elif a.cmd == 'build':
        print(db.build(a.sites, a.levels, a.force).to_string(index=False))
    elif a.cmd == 'discover':
        from .download import active_sites
        known = set(db.raw_sites())
        for site, n in active_sites(a.days).items():
            print(f'{site:30s} {n:6d} {"" if site in known else "NEW"}')
    elif a.cmd == 'status':
        cat = db.catalog
        print(cat[['site', 'level', 'n_records', 'start', 'end', 'wavelengths']].to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
