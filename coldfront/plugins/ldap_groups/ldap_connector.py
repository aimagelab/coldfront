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

    def find_user_by_codice_fiscale(self, codice_fiscale: str) -> Optional[Dict[str, Any]]:
        """Search for a user by Codice Fiscale (employeeNumber attribute).

        Returns a dict with keys 'username' and 'is_expired', or None if not found.
        'is_expired' is True when shadowExpire is set and its date is today or in the past.
        """
        escaped = ldap.filter.escape_filter_chars(codice_fiscale)
        self.conn.search(self.LDAP_USER_SEARCH_BASE, f'(employeeNumber={escaped})',
                         attributes=['uid', 'shadowExpire'])
        if not self.conn.entries:
            return None
        entry = self.conn.entries[0]
        username = entry['uid'].value
        shadow_expire = entry['shadowExpire'].value if entry['shadowExpire'].value is not None else None
        is_expired = False
        if shadow_expire is not None:
            today_epoch = (datetime.date.today() - datetime.date(1970, 1, 1)).days
            is_expired = int(shadow_expire) <= today_epoch
        return {'username': username, 'is_expired': is_expired}

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
        non_structured = {'dottorandi', 'assegnisti', 'collaborazioni', 'contratti_ricerca', 'incarichi_ricerca', 'incarichi_postdoc'}
        structured = {'ricercatori_rtda', 'ricercatori_rtdb', 'ricercatori_rtt', 'professori_associati', 'professori_ordinari'}
        if role in non_structured:
            ou = 'non_strutturati'
        elif role in structured:
            ou = 'strutturati'
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
                    unimore_ldap_username: Optional[str] = None,
                    codice_fiscale: Optional[str] = None) -> str:
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
        if codice_fiscale:
            attrs['employeeNumber'] = codice_fiscale

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
                    first_name: Optional[str] = None,
                    last_name: Optional[str] = None,
                    email: Optional[str] = None,
                    role: Optional[str] = None,
                    password_hash: Optional[str] = None,
                    uid_number: Optional[int] = None,
                    gid_number: Optional[int] = None,
                    home_root: Optional[str] = None,
                    login_shell: Optional[str] = None,
                    mobile: Optional[str] = None,
                    expiration_date: Optional[datetime.date] = None,
                    is_unimore: bool = False,
                    unimore_ldap_username: Optional[str] = None,
                    codice_fiscale: Optional[str] = None,
                    move_if_role_changed: bool = True) -> str:
        """Update a user's attributes using a symmetric API to create_user.

        Only attributes provided (non-None) are modified. If role changes, the DN
        is updated accordingly and primary group (gidNumber) aligned with the role.
        Returns the (possibly new) DN.
        """
        current = self.get_user(username)
        if not current:
            raise ValueError(f'User {username} not found')
        current_dn = current['dn']  # type: ignore[index]

        # If role isn't provided, keep current placement
        role_for_dn = role if role is not None else None
        target_dn = self.build_user_dn(username, role_for_dn) if role_for_dn else current_dn

        modifications: Dict[str, Any] = {}

        if first_name is not None:
            modifications['givenName'] = [(MODIFY_REPLACE, [first_name])]
        if last_name is not None:
            modifications['sn'] = [(MODIFY_REPLACE, [last_name])]
        if (first_name is not None) or (last_name is not None):
            # Recompute CN when either component changes
            cn_val = f"{first_name or current.get('givenName', '')} {last_name or current.get('sn', '')}".strip()
            modifications['cn'] = [(MODIFY_REPLACE, [cn_val])]
        if email is not None:
            modifications['mail'] = [(MODIFY_REPLACE, [email])]
        if login_shell is not None:
            modifications['loginShell'] = [(MODIFY_REPLACE, [login_shell])]
        if home_root is not None:
            modifications['homeDirectory'] = [(MODIFY_REPLACE, [f"{home_root.rstrip('/')}/{username}"])]
        if uid_number is not None:
            modifications['uidNumber'] = [(MODIFY_REPLACE, [str(uid_number)])]
        # Align gidNumber: explicit gid_number wins; else if role provided, ensure role group gid
        if gid_number is not None:
            modifications['gidNumber'] = [(MODIFY_REPLACE, [str(gid_number)])]
        elif role is not None:
            role_gid = self.ensure_group(role)
            modifications['gidNumber'] = [(MODIFY_REPLACE, [str(role_gid)])]
        # Mobile: set if provided; do not delete when None to avoid unintended removal
        if mobile is not None:
            modifications['mobile'] = [(MODIFY_REPLACE, [mobile])]
        if codice_fiscale is not None:
            modifications['employeeNumber'] = [(MODIFY_REPLACE, [codice_fiscale])]

        # Password handling: if UNIMORE, set SASL reference; else replace only if provided
        if is_unimore and unimore_ldap_username:
            modifications['userPassword'] = [(MODIFY_REPLACE, [f"{{SASL}}{unimore_ldap_username}"])]
        elif password_hash is not None:
            modifications['userPassword'] = [(MODIFY_REPLACE, [password_hash])]

        # Shadow / expiration
        if expiration_date is not None:
            modifications['shadowExpire'] = [(MODIFY_REPLACE, [str(self._epoch_days(expiration_date))])]
        # For symmetry with create_user, optionally maintain external account shadow settings
        if not is_unimore and password_hash is not None:
            modifications['shadowLastChange'] = [(MODIFY_REPLACE, ['1'])]
            modifications['shadowMax'] = [(MODIFY_REPLACE, ['30'])]
            modifications['shadowWarning'] = [(MODIFY_REPLACE, ['15'])]

        # Apply modifications if any
        if modifications:
            ok = self.conn.modify(current_dn, modifications)
            if not ok:
                raise RuntimeError(f'Failed to modify user {username}: {self.conn.result}')

        # Move entry if DN should change (e.g., role change)
        if move_if_role_changed and current_dn.lower() != target_dn.lower():
            refreshed = self.get_user(username)
            if not refreshed:  # pragma: no cover
                raise RuntimeError('User disappeared after modify')
            attr_copy = {k: refreshed[k] for k in refreshed.keys() if k not in {'dn'} and refreshed[k]}
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
