import ldap
import ldap.modlist
import argparse
import random
import string
import hashlib
from base64 import b64encode
import copy
import os
import smtplib
from email import message
import datetime
import questionary
import copy

# Env variables
ACCESS_GROUPS = ['ailb-srv']
ROLE_GROUPS = ['dottorandi', 'assegnisti', 'ospiti', 'tesisti', 'past_members', 'administrators', 'collaborazioni', 'studenti', 'contratti_ricerca', 'incarichi_ricerca', 'incarichi_postdoc', 'professori_associati', 'professori_ordinari', 'ricercatori_rtda', 'ricercatori_rtdb', 'ricercatori_rtt']
MATTERMOST_GROUP = 'ailb-mattermost'

# LDAP Connection
base = 'dc=aimagelab,dc=unimore,dc=it'
l = ldap.initialize("ldap://ailb-auth.ing.unimore.it")
l.simple_bind_s("cn=admin,%s" % base,"a7w09lmg")

# User class
class User:
    def __init__(self, l, username):
        self.l = l
        self.uid = None
        self.password_raw = None
        self.username = username
        self.firstname = ''
        self.lastname = ''
        self.email = ''
        self.mobilephone = ''
        self.role = None
        self.isunimore = False
        self.unimoreldap = ''
        self.expiration = ''
        self.ldap_dn = None
        self.is_in_mattermost = False
        self.initialize()

    @property
    def exists(self):
        r = l.search_s("ou=users,%s" % base, ldap.SCOPE_SUBTREE, 'uid='+self.username)
        return len(r) > 0

    def initialize(self):
        if self.exists:
            self.ldap_dn, r = l.search_s("ou=users,%s" % base, ldap.SCOPE_SUBTREE, 'uid='+self.username)[0]
            self.uid = r['uidNumber'][0].decode()
            self.password_raw = r['userPassword'][0].decode()
            self.firstname = r['givenName'][0].decode()
            self.lastname = r['sn'][0].decode()
            self.email = r['mail'][0].decode()
            self.mobilephone = r['mobile'][0].decode() if 'mobile' in r.keys() else ''
            gid = r['gidNumber'][0].decode()
            self.role = l.search_s("ou=groups,%s" % base, ldap.SCOPE_SUBTREE, 'gidNumber='+gid)[-1][-1]['cn'][0].decode()
            groups = l.search_s("ou=groups,%s" % base, ldap.SCOPE_SUBTREE, 'memberUid='+self.username)

            # Sanity check for past members
            if any('past_members' in g[0] for g in groups):
                self.role = 'past_members'

            if any(MATTERMOST_GROUP in g[0] for g in groups):
                self.is_in_mattermost = True

            self.isunimore = r['userPassword'][0].decode().startswith('{SASL}')
            if self.isunimore:
                self.unimoreldap = r['userPassword'][0].decode()[6:]
            self.expiration = str((datetime.datetime.utcfromtimestamp(0) + datetime.timedelta(days=int(r['shadowExpire'][0]))).date())
    
    @property
    def password(self):
        if self.isunimore:
            return "{SASL}" + self.unimoreldap
        else:
            return self.password_raw


    @property
    def gid(self):
        return int(l.search_s("ou=groups,%s" % base, ldap.SCOPE_SUBTREE, 'cn=%s' % self.role)[0][-1]['gidNumber'][0])


    @property
    def dn(self):
        if self.role in ['dottorandi', 'assegnisti', 'collaborazioni', 'contratti_ricerca', 'incarichi_ricerca', 'incarichi_postdoc']:
            ou = 'non_strutturati'
        elif self.role in ['ricercatori_rtda', 'ricercatori_rtdb', 'ricercatori_rtt', 'professori_associati', 'professori_ordinari']:
            ou = 'strutturati'
        else:
            ou = self.role
        dn = "uid=%s,ou=%s,ou=users,%s" % (self.username, ou, base)
        return dn


    @property
    def dict(self):
        shadowExpire = (datetime.datetime.strptime(self.expiration, '%Y-%m-%d') - datetime.datetime.utcfromtimestamp(0)).days
        out = {
           'objectClass': [b"inetOrgPerson", b"posixAccount", b"top", b"shadowAccount", b"ldapPublicKey"],
           'uid': [str.encode(self.username)],
           'sn': [str.encode(self.lastname)],
           'cn': [str.encode('%s %s' % (self.firstname, self.lastname))],
           'givenName': [str.encode(self.firstname)],
           'homeDirectory': [str.encode('/homes/%s' % self.username)],
           'loginShell': [b'/bin/bash'],
           'mail': [str.encode(self.email)],
           'mobile': [str.encode(self.mobilephone)],
           'uidNumber': [str.encode('%s' % self.uid)],
           'gidNumber': [str.encode('%s' % self.gid)],
           'userPassword': [str.encode(self.password)],
           'shadowLastChange': [str.encode("1")],
           'shadowMax': [str.encode("30")],
           'shadowWarning': [str.encode("15")],
           'shadowExpire': [str.encode(str(shadowExpire))]
        }
       
        if self.isunimore:
            out.pop('shadowLastChange', None)
            out.pop('shadowMax', None)

        return out

    def send_welcome_email(self):
        with open('email.txt', 'r') as f:
            text = f.read()
        text = text % (self.firstname, self.username, self.expiration)
        msg = message.Message()
        msg.add_header('from', 'AImageLab-HPC <aimagelab-srv-support@unimore.it>')
        msg.add_header('to', self.email)
        msg.add_header('subject', 'Welcome to AImageLab-HPC!')
        msg.set_payload(text)
        server = smtplib.SMTP()
        server.connect('localhost')
        server.send_message(msg, to_addrs=[self.email, ])
        print("Sent welcome e-mail to user.")


    def send_otp(self, password):
        text = 'Your one-time password for accessing AImageLab-HPC is: %s' % password
        msg = message.Message()
        msg.add_header('from', 'AImageLab-HPC <aimagelab-srv-support@unimore.it>')
        msg.add_header('to', self.email)
        msg.add_header('subject', 'One-time password for AImageLab-HPC')
        msg.set_payload(text)
        server = smtplib.SMTP()
        server.connect('localhost')
        server.send_message(msg, to_addrs=[self.email, ])
        print("Sent OTP to user.")


    def save(self, source):
        def random_password():
            return ''.join(random.choice(string.ascii_letters + string.digits + '!?._-') for _ in range(8))

        if not self.uid:
            action = "create"
        else:
            action = "update"

        # Create password if needed
        if (action == "create" and self.isunimore == False) or (action == "update" and source.isunimore == True and self.isunimore == False):
            password = random_password()
            encoded_password = hashlib.sha1()
            encoded_password.update(str.encode(password))
            self.password_raw = (b"{SHA}" + b64encode(encoded_password.digest())).decode()
            print("Sending OTP password")
            self.send_otp(password)

        if action == "create":
            # Find highest uidNumber
            r = l.search_s("%s" % base, ldap.SCOPE_SUBTREE, 'objectclass=posixaccount')
            self.uid = str(max([int(rr[-1]['uidNumber'][0]) for rr in r]) + 1)
            
            # Save user
            l.add_s(self.dn, ldap.modlist.addModlist(self.dict))
        else:
            if source.ldap_dn != self.dn:
                # Add new entry
                l.add_s(self.dn, ldap.modlist.addModlist(self.dict))
                # Delete old entry
                l.delete_s(source.ldap_dn)
            else:
                # Update entry
                l.modify_s(self.dn, ldap.modlist.modifyModlist(source.dict, self.dict))

        # Add to groups for access and role
        ag = ACCESS_GROUPS if self.role != 'past_members' else []
        mg = [MATTERMOST_GROUP, ] if self.is_in_mattermost else []
        for g in ag + [self.role, ] + mg:
            group_dn = "cn=%s,ou=groups,%s" % (g, base)
            old_group = l.search_s(group_dn, ldap.SCOPE_SUBTREE)[0][-1]
            new_group = copy.deepcopy(old_group)
            if str.encode(self.username) not in new_group['memberUid']:
                new_group['memberUid'].append(str.encode(self.username))
                l.modify_s(group_dn, ldap.modlist.modifyModlist(old_group, new_group))

        # Verify he is not in role groups he shouldn't be
        ag = ACCESS_GROUPS if self.role == 'past_members' else []
        mg = [MATTERMOST_GROUP, ] if self.role == 'past_members' or not self.is_in_mattermost else []
        for g in ag + [rg for rg in ROLE_GROUPS if rg != self.role] + mg:
            group_dn = "cn=%s,ou=groups,%s" % (g, base)
            old_group = l.search_s(group_dn, ldap.SCOPE_SUBTREE)[0][-1]
            new_group = copy.deepcopy(old_group)
            if 'memberUid' in new_group and str.encode(self.username) in new_group['memberUid']:
                new_group['memberUid'].remove(str.encode(self.username))
                l.modify_s(group_dn, ldap.modlist.modifyModlist(old_group, new_group))

        if action == "create":
            # Prevent allocations of new users to nas
            os.system("squota -u %s -f /softechict-nas-1 -q 0.01" % self.username)
            os.system("squota -u %s -f /softechict-nas-2 -q 0.01" % self.username)
            os.system("squota -u %s -f /softechict-nas-3 -q 0.01" % self.username)

        if not os.path.exists('/homes/%s' % self.username):
            print("Home folder does not exist. Creating and populating home folder...")
            os.system("sudo mkdir /homes/%s" % self.username)
            os.system("sudo ./populate_home.sh %s ailb-srv" % self.username)

            # Set quota on home folder
            os.system("squota -u %s -f /homes -q 100" % self.username)

        #if not os.path.exists('/scratch/%s' % self.username):
        #    print("Scratch folder does not exist. Creating it.")
        #    os.system("sudo mkdir /scratch/%s" % self.username)
        #    os.system("sudo chown %s /scratch/%s" % (self.username, self.username))
        #    os.system("sudo chgrp scratch /scratch/%s" % self.username)
        #    os.system("sudo chmod g+s /scratch/%s" % self.username)
        
        if action == "create" or (action == "update" and (source.role != self.role or source.expiration != self.expiration)):
            # Send welcome e-mail
            print("Sending welcome e-mail")
            self.send_welcome_email()


# Ask for username
action = questionary.select(
    "What do you want to do?",
    choices=[questionary.Choice("Add user", "add"), questionary.Choice("Update existing user", "update"), questionary.Choice("Exit", "exit")],
    ).ask()
if action == "exit":
    exit()
username = questionary.text("Username?").ask()
user = User(l, username)
source_user = copy.copy(user)

# Check if user exists
if user.exists and action == 'add':
    print("User already exists.")
    exit()
if action == "update" and not user.exists:
    print("User does not exist.")
    exit()

user.firstname = questionary.text("User first name?", default=user.firstname).ask()
user.lastname = questionary.text("User last name?", default=user.lastname).ask()
user.email = questionary.text("User e-mail?", default=user.email).ask()
user.mobilephone = questionary.text("User mobile phone?", default=user.mobilephone).ask()
user.role = questionary.select("Which is the role of user?", choices=[questionary.Choice("PhD Student", "dottorandi"),
                                                                questionary.Choice("Research grant", "assegnisti"),
                                                                questionary.Choice("Research contract", "collaborazioni"),
                                                                questionary.Choice("Guest", "ospiti"),
                                                                questionary.Choice("Structured personnel", "strutturati"),
                                                                questionary.Choice("Student doing a thesis", "tesisti"),
                                                                questionary.Choice("Student from a course", "studenti"),
                                                                questionary.Choice("Deactivated user", "past_members"),
                                                            ], default=user.role).ask()
user.isunimore = questionary.select("Is the user in UNIMORE LDAP?", choices=[questionary.Choice("Yes", True),
                                                                        questionary.Choice("No", False),
                                                                ], default=user.isunimore).ask()
if user.isunimore:
    user.unimoreldap = questionary.text("What is his UNIMORE LDAP username?", default=user.unimoreldap).ask()

user.expiration = questionary.text("What is his expiration date (YYYY-MM-DD)?", default=user.expiration).ask()
user.is_in_mattermost = questionary.select("Does the user need to be enrolled in Mattermost?", choices=[questionary.Choice("Yes", True),
                                                                        questionary.Choice("No", False),
                                                                ], default=user.is_in_mattermost).ask()
save = questionary.confirm("Save?").ask()
if save:
    user.save(source_user)
