#!/usr/bin/env python3
"""Inspect official signed APT candidates; discovery is not a gateway release."""
import json
import os
import re
import subprocess


def candidates():
    os.environ['LC_ALL']='C'
    subprocess.run(['dpkg','--add-architecture','armhf'],check=True)
    subprocess.run(['apt-get','-o','APT::Sandbox::User=root','update'],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    packages={}
    for name in ('zabbix-agent2','zabbix-proxy-sqlite3'):
        output=subprocess.check_output(['apt-cache','policy',name+':armhf'],text=True)
        match=re.search(r'^\s*Candidate:\s*(\S+)',output,re.M)
        version=match[1] if match and match[1]!='(none)' else None
        packages[name]=version
    return dict(architecture='armhf',candidates=packages,
                zabbix_7_4_available=all(v and v.split(':')[-1].startswith('7.4.') for v in packages.values()),
                image_build_tested=False,gateway_tested=False,supported=False)


if __name__=='__main__':
    print(json.dumps(candidates(),sort_keys=True))
