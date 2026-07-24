# Dashboard — Fase 12

Este documento é a versão em prosa de `app/api/routes/{web_auth,
dashboard}.py` (a fonte de verdade é o código — mantenha os dois
sincronizados).

## Escopo e segurança operacional

A primeira superfície visual do projeto (Fase 12) era inteiramente
**somente leitura**: visualiza o modo do sistema, paper trades, live
trades, modelos de ML registrados e o log de auditoria. Hoje há três
exceções deliberadas: `/dashboard/mode` muda o modo do sistema com
confirmação explícita; `/dashboard/settings/aisa` salva a credencial
MarketPulse de forma mascarada e auditada; `/dashboard/mt5` controla o
plano de sincronização somente leitura executado pelo worker Windows.

`/dashboard/analysis` continua estritamente consultivo. Ele chama o mesmo
serviço determinístico da API/CLI, mas nunca importa ou aciona a camada de
execução. O threshold da tela é sempre no mínimo 90.

Continuam exclusivos da CLI: aprovar um modelo (`ml train --approve`) e
rodar paper/demo trading — qualquer ação que exigiria
uma camada de confirmação/autorização mais pesada do que "digitar o
nome do modo-alvo" ainda não existe pela web.

## Autenticação do dashboard: cookie, não header

A API (`/api/auth/login`, Fase 1) sempre autenticou via
`Authorization: Bearer <jwt>` — funciona bem para clientes programáticos,
mas o navegador não anexa esse header sozinho numa navegação normal de
página HTML. O dashboard reusa o **mesmo** JWT/segredo
(`create_access_token`/`decode_access_token`, `app/core/security.py`),
apenas transportado de forma diferente: um cookie `httpOnly` chamado
`access_token`, lido por `get_current_user_for_web`
(`app/api/dependencies/auth.py`).

Não existe um segundo mecanismo de autenticação paralelo — apenas um
segundo *transporte* para o mesmo token. Diferença de comportamento em
caso de falha: a API devolve `401` (cliente programático trata o
código); o dashboard levanta `RedirectToLogin`, traduzida por um
exception handler (`app/api/app.py`) num redirecionamento HTTP para
`/login` — nunca um 401 cru numa página HTML.

```text
POST /login (form: username, password)
    -> credenciais invalidas: 401 + pagina de login com mensagem de erro
       (e uma entrada de auditoria FAILURE, mesmo padrao de /api/auth/login)
    -> credenciais validas: 303 -> /dashboard + cookie httpOnly `access_token`

GET /dashboard, /dashboard/paper-trades, /dashboard/live-trades,
    /dashboard/models, /dashboard/audit-log, /dashboard/drift,
    /dashboard/mode, /dashboard/analysis, /dashboard/mt5,
    /dashboard/settings/aisa
    -> sem cookie valido: 302 -> /login
    -> cookie valido: 200 + pagina renderizada

POST /dashboard/mode (form: target_mode, confirm_text, reason)
    -> confirm_text != target_mode (case-insensitive): 303 -> /dashboard/mode?error=...
       (nada e alterado)
    -> transicao invalida (mesma validacao de `app.core.system_mode.
       validate_transition` -- inclui REAL_LOCKED/REAL_ENABLED, sempre
       bloqueados, mesmo que alguem poste o valor diretamente sem passar
       pelo <select>): 303 -> /dashboard/mode?error=... (nada e alterado)
    -> transicao valida + confirmacao correta: persiste o modo, grava
       auditoria (mesma `set_mode` usada por `app.cli mode set`),
       303 -> /dashboard/mode?changed_to=<modo>

POST /logout
    -> limpa o cookie, 303 -> /login
```

## Páginas

| Rota | Conteúdo |
|---|---|
| `/dashboard` | Modo atual do sistema + 5 mais recentes de paper trades, live trades e auditoria. |
| `/dashboard/analysis` | Seletor de símbolo/timeframe + relatório profissional com score, nove timeframes, evidências, bloqueios e níveis técnicos. Não envia ordens. |
| `/dashboard/markets` | Catálogo de 32 pares/metais, status de sincronização, busca e seleção para automação; inclui XAU/USD e XAG/USD. |
| `/dashboard/mt5` | Controle do conector Windows: heartbeat, terminal/conta, ativar/pausar/testar/atualizar, seleção de ativos, frequência, backfill e ticks. |
| `/dashboard/market-data` | Cobertura de candles e ticks por símbolo/timeframe. |
| `/dashboard/paper-trades` | Até 100 paper trades mais recentes, todos os símbolos/estratégias. |
| `/dashboard/live-trades` | Até 100 live trades mais recentes — inclui os rejeitados pelo risco/broker (`RISK_REJECTED`/`REJECTED`), não só os que abriram posição. |
| `/dashboard/models` | Modelos de ML registrados (`app.ml.registry.ModelRegistry`), com a versão `current` destacada. |
| `/dashboard/audit-log` | Até 100 entradas de auditoria mais recentes. |
| `/dashboard/drift` | Até 100 eventos de drift de features/métricas/feed mais recentes. |
| `/dashboard/mode` | **Muda estado** (Fase 16): mostra o modo atual + as transições permitidas a partir dele (calculadas chamando `validate_transition` para cada `SystemMode`, nunca uma cópia da regra), e um formulário que exige digitar o nome do modo-alvo como confirmação antes de aplicar. |
| `/dashboard/settings/aisa` | **Muda configuração**: salva/remove a chave MarketPulse e a URL base; nunca reexibe ou registra a chave em texto puro. |

## Workbench de moedas

O seletor é preenchido por `SymbolRepository.list_active()`: aparecem apenas
os símbolos ativos já sincronizados do MT5, preservando nomes e sufixos reais
da corretora. A análise simultânea usa `MN1, W1, D1, H4, H1, M30, M15, M5,
M1`.

`app.market.catalog` mantém o universo de descoberta separado dos símbolos
negociáveis. Isso permite mostrar majors, crosses, exóticos, XAU/USD e
XAG/USD sem fabricar `point`, `digits`, spread ou tamanho de contrato. Um item
só recebe status **Pronto** quando seu nome é reconciliado com um `Symbol`
ativo vindo do MT5; prefixos/sufixos usuais da corretora são preservados.

No modo profissional (`threshold >= 90`), além do score composto, existem
gates rígidos:

- dados suficientes nos nove timeframes;
- notícias e fundamentos/macro com resposta válida do MarketPulse;
- volume com score mínimo 60;
- nenhuma notícia `HIGH` nos próximos 60 minutos;
- score total mínimo 90.

Se qualquer gate falhar, o relatório retorna **NÃO OPERAR**, lista os motivos
e não calcula entrada/stop/alvos. O MarketPulse usa os endpoints oficiais
`/apis/v1/financial/news` e `/apis/v1/financial/financial-metrics`; respostas
sem campos reconhecidos e falhas HTTP permanecem neutras e explícitas.

`PaperTradeRepository.list_all_recent`/`LiveTradeRepository.
list_all_recent` (novos nesta fase) fazem um `JOIN` com `symbols` para
resolver o nome do símbolo numa única query — evita N+1 queries ao
montar cada tabela.

## Stack e decisão de arquitetura

FastAPI + Jinja2 + Bootstrap (via CDN), decisão registrada em
`docs/architecture.md` seção 6 desde a Fase 1: um único processo Python,
deploy simples, sem build step, fácil de depurar no Windows — o
critério que mais pesou para este projeto frente a FastAPI + React.
Templates em `app/api/templates/` (`base.html` com a barra de navegação
comum; `login.html`; `dashboard/*.html` por página).

A sincronização MT5 é a exceção intencional ao processo único: a extensão
oficial só funciona no Windows, portanto `app.mt5.auto_sync` roda na sessão
do usuário como tarefa agendada e compartilha o MySQL com o dashboard Docker.
Configuração/heartbeat usam `system_settings`; credenciais da corretora não
passam por esse canal.

## Limitações e decisões conhecidas

- Sem paginação nas tabelas (limite fixo de 100 linhas) — suficiente
  para o volume desta fase; revisitar se o histórico crescer muito.
- Sem métricas de backtest/walk-forward persistidas — não existe uma
  tabela `backtest_runs` ainda (prevista no catálogo-alvo,
  `docs/data-model.md`); a página `/dashboard/models` cobre a parte de
  "métricas" que já é persistida (registro de modelos de ML).
- O status MT5 usa polling autenticado a cada cinco segundos em vez de
  WebSocket. A coleta em si é contínua e independente da página estar aberta.
- Cookie de sessão sem renovação automática — expira junto com o JWT
  (`AUTH_ACCESS_TOKEN_EXPIRE_MINUTES`), exigindo novo login.
- `/dashboard/mode` (Fase 16) não tem token CSRF dedicado — a proteção
  vem do cookie de sessão já ser `SameSite=Lax` (`app/api/routes/
  web_auth.py`), que o navegador já não anexa em POST disparado por
  outra origem. Se no futuro mais rotas de mutação forem adicionadas,
  vale revisitar e adicionar um token CSRF explícito em vez de depender
  só do `SameSite`.
