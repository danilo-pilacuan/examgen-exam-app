#!/bin/sh
set -e

# Apply DB migrations before starting.
echo "Applying Alembic migrations..."
alembic upgrade head

echo "Starting the API..."
exec uvicorn exam_app.main:app --host 0.0.0.0 --port 8000
