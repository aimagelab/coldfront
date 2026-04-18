import hashlib
import logging
import random
import string
from base64 import b64encode

from django.conf import settings

from coldfront.core.utils.common import import_from_settings

UNIX_GROUP_ATTRIBUTE_NAME = import_from_settings('STORAGE_GROUP_ATTRIBUTE_NAME', 'Storage_Group_Name')
STORAGE_QUOTA_ATTRIBUTE_NAME = import_from_settings('STORAGE_QUOTA_ATTRIBUTE_NAME', 'Storage Quota (GB)')
FILESYSTEM_ATTRIBUTE_NAME = import_from_settings('FILESYSTEM_ATTRIBUTE_NAME', 'Storage filesystem')
LDAP_NOOP = import_from_settings('LDAP_NOOP', False)

logger = logging.getLogger(__name__)

_PASSWORD_CHARS = string.ascii_letters + string.digits + '!?._-'

ACCESS_GROUPS = getattr(settings, 'LDAP_ACCESS_GROUPS', ['ailb-srv'])

ROLE_GROUPS_MAP = getattr(settings, 'LDAP_ROLE_GROUPS_MAP', {
    'Studente di Dottorato': 'dottorandi',
    'Assegno di Ricerca': 'assegnisti',
    'Contratto di Ricerca': 'contratti_ricerca',
    'Incarico di Ricerca': 'incarichi_ricerca',
    'Incarico Post-Doc': 'incarichi_postdoc',
    'Incarico di Collaborazione': 'collaborazioni',
    'Ricercatore RTD-A': 'ricercatori_rtda',
    'Ricercatore RTD-B': 'ricercatori_rtd',
    'Ricercatore RTT': 'ricercatori_rtt',
    'Professore Associato': 'professori_associati',
    'Professore Ordinario': 'professori_ordinari',
    'Studente in tesi': 'tesisti',
    'Studente da un corso': 'studenti',
    'Ospiti a vario titolo': 'ospiti',
    'Deactivated user': 'past_members',
})


def make_sha_password(raw: str) -> str:
    sha = hashlib.sha1()
    sha.update(raw.encode())
    return '{SHA}' + b64encode(sha.digest()).decode()


def random_password(length: int = 12) -> str:
    return ''.join(random.choice(_PASSWORD_CHARS) for _ in range(length))


class StorageGroupsError(Exception):
    pass

class AlreadyMemberError(StorageGroupsError):
    pass

class NotMemberError(StorageGroupsError):
    pass
