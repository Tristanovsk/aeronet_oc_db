'''
Location of the AERONET-OC database, shared by all the packages that use AERONET-OC data.

Resolution order:

1. ``$AERONET_OC_ROOT``;
2. ``root`` in ``$XDG_CONFIG_HOME/aeronet_oc_db/config.toml`` (default ``~/.config/aeronet_oc_db/config.toml``),
   read by the processes that do not inherit the shell environment (Jupyter, PyCharm, cron)::

       root = "/DATA/AERONET/OCv3"

3. ``/DATA/AERONET/OCv3``.
'''
import os
import tomllib
from pathlib import Path

FALLBACK_ROOT = Path('/DATA/AERONET/OCv3')


def config_file():
    base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return base / 'aeronet_oc_db' / 'config.toml'


def default_root():
    '''root folder of the database (see the module documentation)'''
    if os.environ.get('AERONET_OC_ROOT'):
        return Path(os.environ['AERONET_OC_ROOT']).expanduser()
    f = config_file()
    if f.exists():
        with open(f, 'rb') as fh:
            root = tomllib.load(fh).get('root')
        if root:
            return Path(root).expanduser()
    return FALLBACK_ROOT
