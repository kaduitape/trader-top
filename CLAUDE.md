# Convenções deste repositório

Fatos operacionais que se perdem quando o ambiente é recriado. Para
arquitetura, ver `docs/`.

## Branches e deploy

Trabalho em `main` e envio para `master` **automaticamente**, sem pedir aval
a cada vez — instrução permanente do dono do repositório.

`master` dispara `.github/workflows/deploy.yml`, que faz **deploy em
produção** na VPS (SCP + `docker compose up`). Ou seja: todo push para
`master` sobe para produção. Depois de enviar, conferir o resultado do run —
um deploy vermelho precisa ser reportado, não descoberto depois.

As duas branches ficam no mesmo commit. `master` também carrega o workflow
de deploy, que não existe em `main`.

## Ambiente

- **Python 3.13** (`python3.13 -m venv .venv`). O `pyproject` exige `>=3.12`,
  e o `python3` padrão da imagem é 3.11 — instalar com ele falha.
- `rpyc` está **pinado em 5.2.3** e não pode subir para 6.x: a imagem do
  MetaTrader executa o servidor em 5.2.3, e o 6 muda o protocolo. O sintoma
  é `invalid message type: 18` depois de a porta já ter aceitado a conexão.
- Testes: `.venv/bin/python -m pytest -q`. Lint: `.venv/bin/ruff check app tests`.

## Banco de testes

`APP_ENV=test` usa **SQLite em memória compartilhado pela suíte inteira**.
Todo módulo de teste que grava dados precisa de um fixture que limpe **antes
e depois** — inclusive `ticks` e as linhas de `audit_log` das ações que ele
gera. O SQLite não aplica `ON DELETE CASCADE` por padrão, então apagar o
símbolo não apaga os ticks dele.

## Infraestrutura da VPS

- O painel é público em **`https://trader-top.navit.com.br`**, roteado pelo
  Traefik. A porta 8000 escuta só em `127.0.0.1` — `http://<ip>:8000` não
  alcança mais nada de fora. Qualquer instrução com a URL antiga está errada.

- O MetaTrader roda no container `metatrader-5-9p2b-mt5-1`
  (imagem `gmag11/metatrader5_vnc`), **fora** do projeto `trader-top`. O
  servidor RPyC dele é a porta **8001**, não 18812; a 3000 é o noVNC.
- O pipeline sobe apenas `db` e `app`, e **falha de propósito** se encontrar
  `mt5-wine` ou `mt5-worker` no projeto — esses serviços estão atrás do
  profile `local-mt5` e não devem subir nessa VPS.
- Para o painel enxergar o MetaTrader, `MT5_NETWORK` no `.env` aponta para a
  rede dele e o deploy inclui `docker-compose.mt5-network.yml`.

## Vocabulário que não pode escorregar

O score do FotoAnálise/Pulso é **confluência**, nunca probabilidade de lucro.
Não há backtest neste projeto que sustente a segunda leitura, e um teste
(`test_nothing_claims_probability_of_profit`) trava o contrato.
