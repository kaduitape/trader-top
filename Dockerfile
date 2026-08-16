# Imagem Linux para a API/dashboard/CLI (backtest, ML, monitor, preflight).
#
# O pacote oficial `MetaTrader5` continua restrito ao Windows. No Linux, o
# app e o conector usam `mt5linux` para acessar um terminal externo por RPyC.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libs de sistema exigidas por dependencias com extensao nativa (cryptography)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

# Dependencias ficam numa camada que so muda quando o pyproject muda. Alterar
# templates/CSS/Python da aplicacao nao baixa novamente o stack cientifico.
RUN python -c "import subprocess, sys, tomllib; project = tomllib.load(open('pyproject.toml', 'rb')); subprocess.check_call([sys.executable, '-m', 'pip', 'install', *project['project']['dependencies']])"

COPY app ./app
COPY main.py ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker/entrypoint.sh /entrypoint.sh

RUN pip install --no-cache-dir --no-deps . \
    && sed -i 's/\r$//' /entrypoint.sh \
    && chmod +x /entrypoint.sh

RUN mkdir -p logs models datasets

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
