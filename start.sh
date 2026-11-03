#!/bin/sh
set -eu
if [ -n "${TLS_CERT_FILE:-}" ] || [ -n "${TLS_KEY_FILE:-}" ]; then
  if [ ! -r "${TLS_CERT_FILE:-/not/found}" ] || [ ! -r "${TLS_KEY_FILE:-/not/found}" ]; then
    echo "HTTPS cert/key missing or unreadable" >&2; exit 1
  fi
  exec gunicorn -w 2 -b 0.0.0.0:8080 --timeout 90 --certfile "$TLS_CERT_FILE" --keyfile "$TLS_KEY_FILE" app:app
fi
exec gunicorn -w 2 -b 0.0.0.0:8080 --timeout 90 app:app
