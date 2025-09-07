import logging
import datetime
from typing import Optional, Dict, Any, Iterable

import ldap.filter
from coldfront.core.utils.common import import_from_settings
from django.core.exceptions import ImproperlyConfigured
from ldap3 import Connection, Server, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE, SUBTREE
from coldfront.plugins.ldap_groups.utils import AlreadyMemberError, NotMemberError

logger = logging.getLogger(__name__)

class LDAP:
    def __init__(self):
        super().__init__()
        self.LDAP_SERVER_URI = import_from_settings('LDAP_USER_SEARCH_SERVER_URI')
        self.LDAP_USER_SEARCH_BASE = import_from_settings('LDAP_USER_SEARCH_BASE')
        self.LDAP_GROUP_SEARCH_BASE = import_from_settings('LDAP_GROUP_SEARCH_BASE')
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
    
    def get_email(self, username):
        search_filter='(|(&(objectClass=*)(uid=%s)))' % username
        self.conn.search(self.LDAP_USER_SEARCH_BASE, search_filter, attributes=['mail',])
        return self.conn.entries[0]['mail'][0]
    
    # ---------------------------------------------------------------------
    # User CRUD operations needed for onboarding / account provisioning
    # (reimplementation of the LDAP-only logic from legacy add_user.py)
    # ---------------------------------------------------------------------
    def user_exists(self, username: str) -> bool:
        """Return True if a user (uid) exists under the user search base."""
        flt = f'(uid={ldap.filter.escape_filter_chars(username)})'
        self.conn.search(self.LDAP_USER_SEARCH_BASE, flt, attributes=['uid'])
        return len(self.conn.entries) > 0

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Return a dict of the user's LDAP attributes or None."""
        flt = f'(uid={ldap.filter.escape_filter_chars(username)})'
        self.conn.search(self.LDAP_USER_SEARCH_BASE, flt, attributes=['*'])
        if not self.conn.entries:
            return None
        entry = self.conn.entries[0]
        # Convert to simple python dict (values may be lists)
        out: Dict[str, Any] = {}
        for attr in entry.entry_attributes:  # type: ignore[attr-defined]
            values = entry[attr].value  # type: ignore[index]
            out[attr] = values
        out['dn'] = entry.entry_dn  # type: ignore[attr-defined]
        return out

    def _epoch_days(self, date_obj: datetime.date) -> int:
        return (date_obj - datetime.datetime.utcfromtimestamp(0).date()).days

    def get_next_uid_number(self) -> int:
        """Scan for highest uidNumber and return next available."""
        self.conn.search(self.LDAP_USER_SEARCH_BASE, '(objectClass=posixAccount)', attributes=['uidNumber'])
        if not self.conn.entries:
            # Start somewhere sane if directory empty
            return 10000
        max_uid = max(int(e['uidNumber'].value) for e in self.conn.entries if e['uidNumber'].value is not None)
        return max_uid + 1

    def get_next_gid_number(self) -> int:
        self.conn.search(self.LDAP_GROUP_SEARCH_BASE, '(objectClass=posixGroup)', attributes=['gidNumber'])
        if not self.conn.entries:
            return 10000
        max_gid = max(int(e['gidNumber'].value) for e in self.conn.entries if e['gidNumber'].value is not None)
        return max_gid + 1

    def ensure_group(self, group_cn: str, description: str = 'Group account, created by ColdFront') -> int:
        """Ensure a posixGroup exists; return its gidNumber."""
        flt = f'(cn={ldap.filter.escape_filter_chars(group_cn)})'
        self.conn.search(self.LDAP_GROUP_SEARCH_BASE, flt, attributes=['gidNumber'])
        if self.conn.entries:
            return int(self.conn.entries[0]['gidNumber'].value)
        gid = self.get_next_gid_number()
        dn = f'cn={group_cn},{self.LDAP_GROUP_SEARCH_BASE}'
        self.conn.add(dn, ['posixGroup'], {'gidNumber': str(gid), 'description': description, 'memberUid': []})
        if not self.conn.result['description'] == 'success':  # pragma: no cover (defensive)
            raise RuntimeError(f'Failed to create group {group_cn}: {self.conn.result}')
        return gid

    def build_user_dn(self, username: str, role: str) -> str:
        """Replicate OU placement logic from legacy script.
        Certain roles are grouped under ou=non_strutturati; others directly under their role.
        """
        non_structured = {'dottorandi', 'assegnisti', 'collaborazioni'}
        if role in non_structured:
            ou = 'non_strutturati'
        else:
            ou = role
        # user search base typically like 'ou=users,dc=example,dc=org'
        # Insert ou=<ou> directly beneath ou=users
        if not self.LDAP_USER_SEARCH_BASE.lower().startswith('ou=users'):
            # Fallback: place directly in user base
            return f'uid={username},{self.LDAP_USER_SEARCH_BASE}'
        suffix = self.LDAP_USER_SEARCH_BASE.split(',', 1)[1]
        return f'uid={username},ou={ou},ou=users,{suffix}'

    def create_user(self,
                    username: str,
                    first_name: str,
                    last_name: str,
                    email: str,
                    role: str,
                    password_hash: str,
                    uid_number: Optional[int] = None,
                    gid_number: Optional[int] = None,
                    home_root: str = '/homes',
                    login_shell: str = '/bin/bash',
                    mobile: Optional[str] = None,
                    expiration_date: Optional[datetime.date] = None,
                    is_unimore: bool = False,
                    unimore_ldap_username: Optional[str] = None) -> str:
        """Create a new user entry (LDAP-only responsibilities).

        Caller is responsible for generating password_hash (e.g. {SHA} base64...).
        Returns the DN of the created user."""
        if self.user_exists(username):
            raise ValueError(f'User {username} already exists')
        dn = self.build_user_dn(username, role)
        if uid_number is None:
            uid_number = self.get_next_uid_number()
        if gid_number is None:
            gid_number = self.ensure_group(role)

        attrs: Dict[str, Any] = {
            'objectClass': ['inetOrgPerson', 'posixAccount', 'top', 'shadowAccount', 'ldapPublicKey'],
            'uid': username,
            'sn': last_name,
            'cn': f'{first_name} {last_name}'.strip(),
            'givenName': first_name,
            'homeDirectory': f"{home_root.rstrip('/')}/{username}",
            'loginShell': login_shell,
            'mail': email,
            'uidNumber': str(uid_number),
            'gidNumber': str(gid_number),
            'userPassword': password_hash,
        }
        if mobile:
            attrs['mobile'] = mobile
        # Shadow / password policy attributes (skip some for external accounts)
        if not is_unimore:
            attrs.update({
                'shadowLastChange': '1',
                'shadowMax': '30',
                'shadowWarning': '15',
            })
        if expiration_date:
            attrs['shadowExpire'] = str(self._epoch_days(expiration_date))
        elif not is_unimore:
            # Provide a minimal future shadowExpire if not specified (optional)
            pass
        if is_unimore and unimore_ldap_username:
            # Represent remote account style placeholder
            attrs['userPassword'] = f'{{SASL}}{unimore_ldap_username}'

        # ldap3 expects add with attributes as dict of {attr: value/list}
        ok = self.conn.add(dn, attributes=attrs)
        if not ok:
            raise RuntimeError(f'Failed to create user {username}: {self.conn.result}')
        return dn

    def update_user(self,
                    username: str,
                    role: str,
                    new_attrs: Dict[str, Any],
                    move_if_role_changed: bool = True) -> str:
        """Update a user's attributes; if the target DN (due to role change) differs, move entry.

        new_attrs should contain only attributes to change (already with correct formatting / hashing).
        Returns the (possibly new) DN.
        """
        current = self.get_user(username)
        if not current:
            raise ValueError(f'User {username} not found')
        current_dn = current['dn']  # type: ignore[index]
        target_dn = self.build_user_dn(username, role)

        # Prepare modifications: for simplicity we REPLACE provided attributes
        modifications = {}
        for k, v in new_attrs.items():
            if v is None:
                modifications[k] = [(MODIFY_DELETE, [])]
            else:
                modifications[k] = [(MODIFY_REPLACE, [v] if not isinstance(v, (list, tuple)) else list(v))]

        if modifications:
            ok = self.conn.modify(current_dn, modifications)
            if not ok:
                raise RuntimeError(f'Failed to modify user {username}: {self.conn.result}')

        if move_if_role_changed and current_dn.lower() != target_dn.lower():
            # ldap3 modify_dn could be used, but easier to add new + delete to mirror legacy script behavior
            # Fetch full entry after modification
            refreshed = self.get_user(username)
            if not refreshed:  # pragma: no cover
                raise RuntimeError('User disappeared after modify')
            # Build attribute dict for add (excluding operational attributes)
            attr_copy = {k: refreshed[k] for k in refreshed.keys() if k not in {'dn'} and refreshed[k]}
            # Remove existing object
            ok_add = self.conn.add(target_dn, attributes=attr_copy)
            if not ok_add:
                raise RuntimeError(f'Failed to add entry at new DN {target_dn}: {self.conn.result}')
            ok_del = self.conn.delete(current_dn)
            if not ok_del:
                raise RuntimeError(f'Failed to delete old DN {current_dn}: {self.conn.result}')
            return target_dn
        return current_dn

    def add_user_to_groups(self, username: str, groups: Iterable[str]):
        for g in groups:
            try:
                self.group_add_member(g, [username])
            except AlreadyMemberError:
                continue

    def remove_user_from_groups(self, username: str, groups: Iterable[str]):
        for g in groups:
            try:
                self.group_remove_member(g, [username])
            except NotMemberError:
                continue
