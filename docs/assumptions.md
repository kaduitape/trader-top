# Premissas e Decisões — Fase 0

Este documento registra decisões tomadas na Fase 0 (descoberta) e os riscos
conhecidos que resultam delas. Deve ser revisado sempre que uma premissa deixar
de ser válida.

## 1. Ambiente inspecionado

| Item | Resultado observado |
|---|---|
| Sistema operacional | Windows 10 Pro 10.0.19045 |
| Python disponível | 3.14.6 (único interpretador instalado; sem 3.12) |
| Gerenciador de pacotes | pip 26.1.2 |
| MySQL client local | não encontrado |
| Docker | não encontrado |
| Git | disponível (2.39.2), mas repositório **não** inicializado nesta pasta |
| Diretório de trabalho | `d:\WEBSITES\Trader TOP` (vazio antes da Fase 1) |

## 2. Decisões confirmadas com o usuário

### 2.1 Versão do Python — **decisão: usar Python 3.14 (instalado)**

**Atualização (Fase 2):** o risco foi checado como prometido. O pacote
oficial `MetaTrader5` publica wheel `cp314-win_amd64` (versão 5.0.5735) e
instalou/importou sem problemas nesta máquina. `numpy` também instalou
normalmente. O risco de falta de wheel não se concretizou para o conector
MT5. Continuava pendente para `xgboost`/`lightgbm`, a checar na Fase 8.

**Atualização (Fase 8):** risco encerrado. `scikit-learn` (1.9.0),
`xgboost` (3.3.0) e `shap` (0.52.0, via wheel `cp312-abi3` compatível com
3.14) instalaram e importaram sem problemas. `numpy` foi rebaixado de
2.5.1 para 2.4.6 por restrição do `scikit-learn` — suíte de testes
completa (265 testes) revalidada após a mudança, sem regressões.
`lightgbm` não foi instalado por ser explicitamente opcional no prompt
mestre e não haver necessidade concreta ainda (mesma lógica de
`docs/features.md`: não adicionar dependência sem consumidor real).

Também confirmado nesta fase: `mt5.initialize()` retorna `False` nesta
máquina (`last_error() == (-6, 'Terminal: Authorization failed')`) porque
não há terminal MetaTrader 5 instalado/autenticado aqui. Isso é esperado —
nenhuma credencial real foi usada ou inventada (ver seção 5). A suíte de
testes do conector MT5 nunca chama o pacote `MetaTrader5` de verdade; usa
um stub/fake injetado (ver `docs/architecture.md` — a camada `app/mt5` é a
única que importa `MetaTrader5`, e todo o resto do sistema depende de tipos
próprios, o que permite testar sem terminal instalado).


O prompt mestre especifica Python 3.12 como alvo, pois o pacote oficial
`MetaTrader5`, além de `numpy`, `pandas`, `scikit-learn`, `xgboost` e
`lightgbm`, historicamente atrasam a publicação de wheels para versões muito
recentes do Python. O usuário optou explicitamente por seguir com o Python
3.14 já instalado, em vez de instalar 3.12 em paralelo.

**Risco aceito:** ao chegar na Fase 2 (conector MT5) e na Fase 8 (IA), pode
não haver wheel compatível de `MetaTrader5`, `xgboost` ou `lightgbm` para
3.14. Se isso ocorrer, a alternativa será instalar Python 3.12 naquele
momento (via `py install 3.12`) e recriar o ambiente virtual apenas para os
módulos afetados — decisão a ser revisitada quando a Fase 2/8 chegar, não
antes.

**Mitigação adotada agora:** o `pyproject.toml` declara
`requires-python = ">=3.12,<3.15"` (não trava em 3.14), e nenhuma dependência
das Fases 0/1 (FastAPI, SQLAlchemy, Alembic, Pydantic, PyMySQL, Uvicorn,
Passlib, python-jose, pytest, Ruff, Black, MyPy) tem histórico de atraso de
wheels — todas instalaram normalmente em 3.14 nesta máquina (ver seção 4).

### 2.2 MySQL — **decisão: criar campo de configuração, sem exigir servidor real agora**

Não há MySQL nem Docker instalados nesta máquina. O usuário optou por criar a
camada de configuração (variáveis de ambiente, `DATABASE_URL`, engine
SQLAlchemy, Alembic) pronta para apontar a qualquer MySQL 8 (local, remoto ou
em contêiner), sem que a Fase 1 dependa de um servidor real rodando agora.

**Consequência prática:**
- `.env.example` documenta todas as variáveis de conexão (host, porta, usuário,
  senha, nome do banco, opções de SSL).
- Os testes automatizados da Fase 1 **não** exigem MySQL real — usam SQLite
  em memória via `sqlite+pysqlite:///:memory:` apenas para validar que os
  modelos, o `Base` declarativo e as migrations Alembic (lógica de upgrade)
  funcionam mecanicamente. Isso é uma simplificação temporária de teste, não
  uma mudança de banco de produção — o banco de produção continua sendo MySQL 8.
- Um comando de verificação real (`python -m app.cli db check`) é fornecido
  para quando o usuário tiver um MySQL acessível: ele tenta abrir a conexão
  configurada em `.env` e relata sucesso/erro claramente.
- O critério de aceite "banco conecta" desta Fase 1 é reinterpretado como:
  *a camada de conexão existe, é tipada, é testável e funciona contra um
  banco compatível (demonstrado com SQLite nos testes automatizados)* — a
  validação contra MySQL real fica pendente até o usuário disponibilizar um
  servidor, e será executada nesse momento com o comando acima.

### 2.3 Git — **decisão: não inicializar repositório agora**

O usuário pediu para não rodar `git init` nesta fase. Os arquivos permanecem
apenas em disco. Isso será revisitado quando o usuário solicitar.

**Risco aceito:** sem controle de versão, não há histórico de commits para
auditar decisões incrementais nesta fase — a auditabilidade fica documentada
manualmente nestes arquivos de `docs/` em vez de mensagens de commit.

## 3. Bibliotecas avaliadas e adiadas

As dependências de IA (`scikit-learn`, `xgboost`, `lightgbm`, `optuna`, `shap`,
`mlflow`), do conector MT5 (`MetaTrader5`), e de indicadores (`pandas-ta` ou
equivalente) **não** são instaladas nesta fase — só entram em Fases 2 e 8, por
decisão do próprio prompt mestre. Isso reduz a superfície de risco de
compatibilidade nesta fase inicial.

## 4. Dependências validadas nesta máquina (Fase 1)

Todas as dependências abaixo foram instaladas e testadas com Python 3.14.6
nesta máquina, sem necessidade de compilação nativa nem falha de wheel:
`fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `sqlalchemy`,
`alembic`, `pymysql`, `passlib[bcrypt]`, `python-jose[cryptography]`,
`pytest`, `pytest-cov`, `ruff`, `black`, `mypy`, `httpx` (cliente de teste do
FastAPI).

## 5. Fora de escopo nesta execução (reforço das regras do prompt mestre)

Não foram implementados nesta fase, propositalmente:
- conexão com conta MetaTrader real ou demo;
- qualquer chamada a `order_send` ou envio de ordens;
- estratégias de trading;
- treinamento de modelos de IA;
- coleta de candles/ticks.

O modo do sistema (ver `docs/architecture.md`) inicia e permanece em
`DISABLED` até fases futuras.
