# Segurança — MT5 AI Scalper

## 1. Segredos e configuração

- Nenhuma credencial é gravada no código-fonte. Toda credencial (senha de
  banco, senha/servidor MT5, chaves de API, chave de liberação de conta
  real) vem de variáveis de ambiente carregadas via `.env` (nunca commitado).
- `.env.example` documenta todas as variáveis exigidas, com valores
  fictícios claramente marcados como placeholder (nunca credenciais reais).
- `app/core/config.py` usa `pydantic-settings` para carregar e **validar**
  a configuração tipada na inicialização — a aplicação falha rápido (erro
  claro) se uma variável obrigatória estiver ausente, em vez de operar com
  valores indefinidos.
- Logs e alertas nunca imprimem senhas, tokens ou chaves — `app/core/logging.py`
  mascara campos sensíveis por nome (`password`, `secret`, `token`, `key`).

## 2. Autenticação e autorização

- Autenticação básica via usuário/senha com hash `bcrypt` (`passlib`), nunca
  texto puro nem hash reversível.
- Sessão/token via JWT assinado (`python-jose`), com expiração configurável.
- Perfis de autorização (definidos em `app/core/enums.py::UserRole`):
  `VIEWER`, `ANALYST`, `OPERATOR`, `RISK_MANAGER`, `ADMIN`.
- Apenas `ADMIN` (ou perfil dedicado a ser definido na Fase 11) pode
  solicitar liberação de modo real — nunca `OPERATOR` ou perfis inferiores.
- Rotas administrativas exigem dependência de autorização explícita
  (`app/api/dependencies`), não apenas checagem manual dentro do handler.

## 3. Proteções de aplicação

- Rate limiting nas rotas de autenticação (a implementar plenamente quando
  houver rotas de negócio na Fase 2+; a Fase 1 prepara o hook de
  middleware).
- Validação de entrada em todas as rotas via schemas Pydantic — nunca
  parâmetros não tipados.
- Expiração de sessão configurável.
- Proteção contra ativação acidental de modo real: exige múltiplas
  confirmações e nunca uma única flag booleana (ver `docs/architecture.md`
  §4).

## 4. Auditoria

- Tabela `audit_logs` (modelo introduzido nesta fase) registra: usuário,
  ação, entidade afetada, timestamp UTC, resultado. Login, alterações de
  configuração e (futuramente) ativações de modo são sempre auditados.
- Logs estruturados em JSON incluem `correlation_id` para religar eventos
  de um mesmo fluxo (sinal → risco → ordem) sem expor dados sensíveis.

## 5. Backup e recuperação

- A ser detalhado operacionalmente na Fase 15 (preparação operacional).
  Nesta fase, o único ativo a proteger é o schema do banco (via migrations
  versionadas no Alembic, que já funcionam como registro incremental e
  reversível do estado do banco).

## 6. O que este projeto nunca implementa (regras inegociáveis)

Reforço das regras do prompt mestre, válidas para todas as fases:
martingale, soros, grade infinita, aumento automático de lote após
prejuízo, operação sem stop-loss, recuperação compulsiva de perdas,
alteração retroativa de resultados, uso de dados futuros no treinamento,
backtest sem custos, ocultação de trades perdedores, seleção de estratégia
apenas pelo lucro líquido, credenciais no código, ativação automática de
conta real, execução com conexão instável/dados atrasados/risco diário
excedido.
