#!/bin/bash
set -e
~/.acme.sh/acme.sh --install-cert -d macrobiodiv.zbhgis.com --ecc \
  --cert-file      /etc/nginx/ssl/macrobiodiv.zbhgis.com.cer \
  --key-file       /etc/nginx/ssl/macrobiodiv.zbhgis.com.key \
  --fullchain-file /etc/nginx/ssl/macrobiodiv.zbhgis.com.fullchain.cer \
  --reloadcmd      "nginx -s reload"
echo "--- /etc/nginx/ssl ---"
ls -la /etc/nginx/ssl/ | grep macrobiodiv
