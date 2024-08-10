#!/bin/bash

# Chdir and activate env
cd /srv/coldfront;
source venv/bin/activate; 

python3 /srv/coldfront/scripts/send_expiration_email_work.py
