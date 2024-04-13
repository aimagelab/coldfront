from coldfront.config.base import INSTALLED_APPS, ENV
from coldfront.config.env import ENV

INSTALLED_APPS += [
    'coldfront.plugins.ldap_groups',
]
