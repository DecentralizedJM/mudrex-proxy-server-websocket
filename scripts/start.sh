#!/bin/sh
# Use PORT from environment (Railway injects at runtime); default 8000 for local
export PORT="${PORT:-8000}"
exec python -m app.standalone_server
