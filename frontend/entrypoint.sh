#!/bin/sh
cat > /usr/share/nginx/html/config.js << EOF
window.__RUNTIME_CONFIG__ = {
  API_BASE_URL: "${API_BASE_URL}",
  GMAPS_KEY: "${GMAPS_KEY}"
};
EOF
exec nginx -g "daemon off;"
