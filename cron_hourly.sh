#!/bin/bash

# Chdir and activate env
cd /srv/coldfront;
source venv/bin/activate; 

# SLURM sync
coldfront slurm_dump -o ~/slurm_dump
sacctmgr -i load file=~/slurm_dump/aimagelab.cfg
coldfront slurm_check -c aimagelab -s
coldfront slurm_usage -s

# LDAP/quota sync
coldfront ldap_check -s -x
coldfront quotas_check -s -x
