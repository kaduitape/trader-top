# Imagem Linux para a API/dashboard/CLI (backtest, ML, monitor, preflight).
#
# NAO inclui o pacote `MetaTrader5` (extra opcional "mt5" no pyproject.toml):
# esse pacote so publica wheel para Windows, porque fala com o terminal MT5
# via DLL/named pipe -- nao existe (nem pode existir) uma versao Linux dele.
# Consequencia pratica: comandos que dependem de conexao real ao MetaTrader
# (`mt5 check`, `collect candles`/`collect ticks`, `paper run`, `demo run`)
# NAO funcionam dentro deste container -- rode-os no host Windows, com um
# terminal MT5 instalado e autenticado, apontando DB_HOST para o MySQL deste
# compose. Ver docs/runbook.md.
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
