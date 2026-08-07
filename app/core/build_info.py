"""Que versao do codigo este processo esta rodando.

O conector Windows e o servidor web sao processos separados, em maquinas
separadas, cada um com a sua copia do projeto. Nada garante que estejam na
mesma versao — e quando nao estao, o sintoma e o mais frustrante que
existe: "consertei aquilo e continua igual".

## Por que a impressao digital, e nao o sha do git

A primeira versao disto usava `git rev-parse HEAD`, e estava errada de um
jeito que so aparece em producao: `.git/` esta no `.dockerignore`, entao o
painel (que roda em container) NUNCA tem repositorio e caia para a versao
do pacote — "0.1.0" —, enquanto o conector no Windows devolvia um sha. Os
dois nunca batiam, e o alarme ficava ligado para sempre, dizendo justamente
o contrario da verdade.

A licao: o identificador tem que vir do CODIGO, nao do ambiente ao redor
dele. Aqui ele e uma impressao digital do conteudo dos proprios arquivos
`.py` de `app/`. Codigo igual produz identificador igual em qualquer lugar
— container Linux sem git, Windows com git, pasta copiada por pendrive.

Duas normalizacoes que parecem detalhe e nao sao:

- **Fim de linha.** O git no Windows costuma converter LF para CRLF na
  checagem. Sem normalizar, o MESMO commit produziria digitais diferentes
  nos dois sistemas — recriando exatamente o falso alarme que este modulo
  existe para eliminar.
- **Separador de caminho.** `app/core/x.py` e `app\\core\\x.py` sao o mesmo
  arquivo; o caminho entra na conta com barra normal sempre.

Calculado uma vez por processo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

UNKNOWN = "desconhecida"

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_DIR = _ROOT / "app"


def _iter_sources(base: Path) -> Iterator[Path]:
    for caminho in sorted(base.rglob("*.py")):
        if "__pycache__" in caminho.parts:
            continue
        yield caminho


@lru_cache(maxsize=1)
def code_version() -> str:
    """Impressao digital curta do codigo em execucao."""
    if not _SOURCE_DIR.is_dir():
        return UNKNOWN

    digest = hashlib.blake2b(digest_size=8)
    encontrou = False
    for caminho in _iter_sources(_SOURCE_DIR):
        try:
            conteudo = caminho.read_bytes()
        except OSError:
            # Arquivo ilegivel nao pode derrubar o diagnostico; ele so nao
            # entra na conta. A digital continua util para comparar.
            continue
        encontrou = True
        relativo = caminho.relative_to(_ROOT).as_posix()
        digest.update(relativo.encode("utf-8"))
        digest.update(b"\0")
        digest.update(conteudo.replace(b"\r\n", b"\n"))
        digest.update(b"\0")

    return digest.hexdigest() if encontrou else UNKNOWN


def versions_match(a: str | None, b: str | None) -> bool:
    """Duas versoes sao comparaveis e iguais?

    Desconhecido nunca "bate" com nada: alertar sem certeza e melhor que
    silenciar uma divergencia real, porque o custo de investigar um alerta
    falso e menor que o de procurar um bug ja corrigido.
    """
    if not a or not b:
        return False
    if UNKNOWN in (a, b):
        return False
    return a == b
