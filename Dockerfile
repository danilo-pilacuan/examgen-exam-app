FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

# Copiar requirements e instalar dependencias.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copiar los paquetes del monorepo (core, agents-backend, exam-app).
COPY libs/core/src/core /app/core
COPY apps/agents-backend/src/agents_backend /app/agents_backend
COPY apps/exam-app/src/exam_app /app/exam_app
COPY libs/core/src/core/infrastructure/db/migrations /app/libs/core/src/core/infrastructure/db/migrations
COPY scripts/ /app/scripts/
COPY alembic.ini /app/alembic.ini
COPY apps/exam-app/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
