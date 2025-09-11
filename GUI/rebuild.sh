#!/bin/sh

# Install dependencies
apk add nodejs

# Rebuild the project
cd /opt/ckb
./node_modules/.bin/vite build -l warn
cp -ru build/* /usr/share/nginx/html/ckb

# Start the Nginx server
nginx -g "daemon off;"
