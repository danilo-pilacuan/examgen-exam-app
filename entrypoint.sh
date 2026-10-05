#!/bin/sh
set -e

# Aplicar migraciones de la BD antes de arrancar.
echo "Aplicando migraciones Alembic..."
alembic upgrade head

echo "Iniciando la API..."
exec uvicorn exam_app.main:app --host 0.0.0.0 --port 8000
