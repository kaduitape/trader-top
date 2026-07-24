#!/bin/sh
set -e

echo "Aplicando migrations (alembic upgrade head)..."
alembic upgrade head

if [ "${RUN_PREFLIGHT:-true}" = "true" ]; then
    echo "Validando ambiente (preflight check)..."
    # Credenciais MT5 ausentes no Linux sao reportadas como aviso. Falhas
    # reais de banco/migrations/diretorios impedem um container quebrado de
    # ser marcado como pronto.
    python -m app.cli preflight check
fi

exec "$@"
