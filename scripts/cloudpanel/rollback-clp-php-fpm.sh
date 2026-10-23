#!/usr/bin/env bash
# Roll back CloudPanel's clp-php-fpm systemd override to the vendor PHP 8.1 unit.
# Does not modify website PHP-FPM services.
set -euo pipefail

if (( EUID != 0 )); then
  echo "Run as root." >&2
  exit 1
fi

override="/etc/systemd/system/clp-php-fpm.service.d/override.conf"
rm -f -- "$override"
systemctl daemon-reload
systemctl restart clp-php-fpm
systemctl status clp-php-fpm --no-pager
