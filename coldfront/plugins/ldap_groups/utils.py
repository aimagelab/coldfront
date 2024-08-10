import logging
from coldfront.core.utils.common import import_from_settings

UNIX_GROUP_ATTRIBUTE_NAME = import_from_settings('STORAGE_GROUP_ATTRIBUTE_NAME', 'Storage_Group_Name')
STORAGE_QUOTA_ATTRIBUTE_NAME = import_from_settings('STORAGE_QUOTA_ATTRIBUTE_NAME', 'Storage Quota (GB)')
FILESYSTEM_ATTRIBUTE_NAME = import_from_settings('FILESYSTEM_ATTRIBUTE_NAME', 'Storage filesystem')
LDAP_NOOP = import_from_settings('LDAP_NOOP', False)

logger = logging.getLogger(__name__)

class StorageGroupsError(Exception):
    pass

class AlreadyMemberError(StorageGroupsError):
    pass

class NotMemberError(StorageGroupsError):
    pass
