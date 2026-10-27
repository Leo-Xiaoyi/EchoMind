#!/bin/sh
set -eu

cat >/usr/share/nginx/html/runtime-config.js <<EOF
window.__ECHOMIND_CONFIG__ = {
  pythonApiUrl: "${PYTHON_API_URL:-/api/python}"
};
EOF

exec nginx -g "daemon off;"
