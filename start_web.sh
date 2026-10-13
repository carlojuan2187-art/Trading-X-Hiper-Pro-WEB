#!/bin/bash
set -e

echo "🌐 Iniciando Trading X Hiper Pro MiniApp API..."
uvicorn web_main:app --host 0.0.0.0 --port "${PORT:-8000}"
