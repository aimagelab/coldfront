import logging

import ldap.filter
from coldfront.core.utils.common import import_from_settings
from django.core.exceptions import ImproperlyConfigured
from ldap3 import Connection, Server, MODIFY_REPLACE
from coldfront.plugins.ldap_groups.utils import AlreadyMemberError, NotMemberError

logger = logging.getLogger(__name__)

class LDAP:
    def __init__(self):
        super().__init__()
        self.LDAP_SERVER_URI = import_from_settings('LDAP_USER_SEARCH_SERVER_URI')
        self.LDAP_USER_SEARCH_BASE = import_from_settings('LDAP_USER_SEARCH_BASE')
        self.LDAP_GROUP_SEARCH_BASE = import_from_settings('AUTH_LDAP_GROUP_SEARCH_BASE')
        self.LDAP_BIND_DN = import_from_settings('LDAP_USER_SEARCH_BIND_DN', None)
        self.LDAP_BIND_PASSWORD = import_from_settings('LDAP_USER_SEARCH_BIND_PASSWORD', None)
        self.LDAP_CONNECT_TIMEOUT = import_from_settings('LDAP_USER_SEARCH_CONNECT_TIMEOUT', 2.5)
        self.LDAP_USE_SSL = import_from_settings('LDAP_USER_SEARCH_USE_SSL', True)

        self.server = Server(self.LDAP_SERVER_URI, use_ssl=self.LDAP_USE_SSL, connect_timeout=self.LDAP_CONNECT_TIMEOUT)
        self.conn = Connection(self.server, self.LDAP_BIND_DN, self.LDAP_BIND_PASSWORD, auto_bind=True)

        if not self.conn.bind():
            raise ImproperlyConfigured('Failed to bind to LDAP server: {}'.format(self.conn.result))
        else:
            logger.info('LDAP bind successful: %s', self.conn.extend.standard.who_am_i())

    def group_add_member(self, group, user):
        assert(isinstance(user, list) and len(user) == 1)

        group_dn = 'cn=' + group + ',' + self.LDAP_GROUP_SEARCH_BASE
        self.conn.search(self.LDAP_GROUP_SEARCH_BASE, '(cn=' + group + ')', attributes=['memberUid'])

        if len(self.conn.entries) == 0:
            # Find next available gidNumber
            self.conn.search(self.LDAP_GROUP_SEARCH_BASE, '(objectClass=posixGroup)', attributes=['gidNumber'])
            gid_number = max([int(entry['gidNumber'].values[0]) for entry in self.conn.entries]) + 1

            # Create group
            self.conn.add(group_dn, 'posixGroup', {
                'description': 'Group account, created by ColdFront',
                'gidNumber': gid_number,
                'memberUid': user})
        else:
            # Add user to group, if not already a member
            memberUid = self.conn.entries[0]['memberUid'].values

            if user[0] in memberUid:
                raise AlreadyMemberError

            memberUid.extend(user)
            self.conn.modify(group_dn, {'memberUid': [(MODIFY_REPLACE, memberUid)]})

    def group_remove_member(self, group, user):
        assert(isinstance(user, list) and len(user) == 1)

        group_dn = 'cn=' + group + ',' + self.LDAP_GROUP_SEARCH_BASE
        self.conn.search(self.LDAP_GROUP_SEARCH_BASE, '(cn=' + group + ')', attributes=['memberUid'])

        if len(self.conn.entries) == 0:
            # Group does not exist, nothing to do
            return
        else:
            # Remove user from group, if a member
            memberUid = self.conn.entries[0]['memberUid'].values

            if user[0] not in memberUid:
                raise NotMemberError

            memberUid.remove(user[0])
            self.conn.modify(group_dn, {'memberUid': [(MODIFY_REPLACE, memberUid)]})

    def get_groups_of_user(self, username):
        search_filter='(|(&(objectClass=*)(memberUid=%s)))' % username
        self.conn.search(self.LDAP_GROUP_SEARCH_BASE, search_filter, attributes=['cn',])
        return [entry['cn'][0] for entry in self.conn.entries]
    