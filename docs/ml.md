# Machine Learning — Fase 8

Este documento é a versão em prosa do pipeline em `app/ml/` (a fonte de
verdade é o código — mantenha os dois sincronizados).

## O problema de ML (o que este pipeline NÃO faz)

Conforme o prompt mestre (seção 12), a Fase 8 **não tenta prever preço
futuro**. O problema é outro, mais restrito e mais honesto:

> Dado um sinal válido já gerado por uma estratégia (Fase 6), qual é a
> probabilidade de o alvo (`take_profit`) ser atingido antes do stop
> (`stop_loss`), dentro de um horizonte definido?

Isso significa que o modelo nunca decide *quando* entrar — quem decide
isso continua sendo a `Strategy` (Fase 5/6). O modelo apenas estima a
qualidade esperada de um sinal que a estratégia já geraria de qualquer
forma. Um modelo aprovado poderia, em fases futuras (Fase 11+), ser usado
como um filtro adicional (ex.: só operar sinais com probabilidade
prevista acima de um limiar) — essa integração ainda não existe.

## Pipeline, módulo por módulo

| Módulo | Responsabilidade |
|---|---|
| `app/ml/labels.py` | Rotulagem por barreira tripla (`apply_triple_barrier`). |
| `app/ml/datasets.py` | Constrói uma linha por sinal real da estratégia (`build_signal_dataset`). |
| `app/ml/splits.py` | Divisão treino/teste cronológica com embargo (`temporal_train_test_split`). |
| `app/ml/preprocessing.py` | `ColumnTransformer` (escala + one-hot), ajustado só no treino. |
| `app/ml/train.py` | Regressão logística, Random Forest, HistGradientBoosting, XGBoost. |
| `app/ml/calibration.py` | Calibração de probabilidades (`FrozenEstimator` + `CalibratedClassifierCV`). |
| `app/ml/validation.py` | Métricas de classificação + métricas de trading após custos. |
| `app/ml/explainability.py` | SHAP (árvores) / coeficientes (regressão logística). |
| `app/ml/registry.py` | Versionamento, serialização (`joblib`) e rollback. |

## Rotulagem por barreira tripla (`labels.py`)

As barreiras superior/inferior **são o próprio `take_profit`/`stop_loss`
do sinal da estratégia** — não um limiar arbitrário — para que o rótulo
responda exatamente à pergunta acima. Três desfechos possíveis:

- `TARGET_FIRST` (`label=1`): o alvo foi atingido antes do stop.
- `STOP_FIRST` (`label=0`): o stop foi atingido antes do alvo.
- `TIME_BARRIER` (`label=0`): nem um nem outro dentro do horizonte máximo
  — tratado conservadoramente como "não operar", nunca como um terceiro
  valor a ser modelado à parte.

Usa a **mesma regra conservadora das Fases 5/6**: se ambas as barreiras
cabem na mesma candle, assume-se o stop (o pior caso), nunca o resultado
favorável. Retorna `None` (nunca inventa um desfecho) quando não há
barras suficientes após o sinal — essas amostras simplesmente não entram
no dataset.

## Construção do dataset (`datasets.py`)

Reusa a mesma regra de "uma posição por vez" do motor de backtest por
candle (Fase 5): o próximo sinal só é avaliado depois que o anterior foi
resolvido (alvo, stop ou limite de tempo). Isso garante que as janelas de
barreira tripla de duas amostras consecutivas **nunca se sobrepõem** —
exigência literal do prompt mestre contra "sobreposição indevida entre
amostras".

Uma estratégia só é avaliada a partir da barra `required_lookback_bars()
- 1` (hoje, 199) — antes disso, features como `dist_ema_200` ainda são
`NaN` (a EMA200 não "esquentou"). Gerar uma amostra com features
incompletas corromperia o dataset e quebraria modelos que não aceitam
`NaN` nativamente (regressão logística, Random Forest); em vez de
imputar um valor, essas barras são simplesmente puladas (bug real,
encontrado pelos próprios testes de integração da CLI — ver seção
"Limitações e decisões conhecidas").

### Features incluídas (`ML_NUMERIC_FEATURE_COLUMNS`/`ML_CATEGORICAL_FEATURE_COLUMNS`)

Deliberadamente **excluídos os níveis de preço absolutos** (`ema_9/21/50/200`,
`bollinger_upper/middle/lower`, `open/high/low/close` brutos) — não
generalizam entre símbolos/períodos diferentes (ver `docs/features.md`).
Em vez disso, entram apenas distâncias relativas (`dist_ema_*`),
osciladores (`rsi_14`, `adx_14`, `zscore_20`...), retornos e derivados de
tempo/sessão. `session` é a única feature categórica (one-hot).

**Limitação conhecida:** `macd_line`/`macd_signal`/`macd_histogram`,
`momentum_10` e as features de forma do candle (`candle_amplitude`,
`candle_body`, `candle_upper_wick`, `candle_lower_wick`) permanecem em
escala de preço (não normalizadas) — diferente de `dist_ema_*`, que já é
uma razão. Normalizá-las (ex.: dividir por `atr_14`) fica para quando um
modelo real precisar operar em múltiplos símbolos com escalas de preço
muito diferentes; não há esse consumidor concreto ainda.

**Limitação conhecida:** `entry_spread` é o spread no momento da
**entrada**; a métrica de trading (`validation.py`) reusa esse mesmo
valor como aproximação do spread de **saída** por não haver uma coluna
separada no dataset — conservador o suficiente na prática (spreads de
saída raramente são menores), mas não é uma medição exata.

## Divisão temporal com embargo (`splits.py`)

Sempre ordenado cronologicamente (nunca embaralhado). O embargo remove as
últimas `embargo_samples` linhas do treino, mais próximas do corte, para
reduzir a chance de uma amostra de treino cuja janela de barreira tripla
se estenda para dentro do período de teste. Walk-forward completo
(múltiplas janelas deslizantes) é a Fase 9 — aqui é uma única divisão.

## Treino e calibração

`train.py` treina o modelo escolhido só na fatia de ajuste
(`split_fit_calibration.x_fit`/`y_fit`); `calibration.py` calibra as
probabilidades numa fatia **separada** (`x_calib`/`y_calib`, nunca vista
pelo treino) usando `sklearn.frozen.FrozenEstimator` +
`CalibratedClassifierCV` — a API que substituiu `cv="prefit"` (removida
no sklearn 1.9, confirmado por inspeção direta do pacote instalado antes
de escrever o módulo). Desbalanceamento de classes é tratado via
`class_weight="balanced"` (regressão logística/Random Forest) ou
`sample_weight` calculado (HistGradientBoosting/XGBoost, que não aceitam
`class_weight`).

## Validação (`validation.py`)

Duas famílias de métricas, nunca reduzidas a um único número:

- **Classificação**: precisão, recall, F1, ROC-AUC, PR-AUC, Brier score,
  log-loss — `None` (nunca um valor fabricado) quando uma classe está
  ausente no conjunto avaliado.
- **Trading após custos**: filtra o conjunto de teste pelas linhas com
  probabilidade prevista ≥ limiar e recalcula o resultado econômico real
  dessas linhas, aplicando `app.backtesting.costs` (spread, slippage,
  comissão) — a mesma lógica de custo do backtester, não uma fórmula
  paralela. Inclui intervalo de confiança da expectativa (aproximação
  normal) e resultado segmentado por regime de tendência.

## Explicabilidade (`explainability.py`)

SHAP (`TreeExplainer`) para modelos em árvore; coeficientes brutos para
regressão logística. Observação empírica registrada no código (shap
0.52, verificada por inspeção direta antes de escrever o módulo):
`shap_values` retorna um array 3D (`amostras × features × classes`) para
`RandomForestClassifier`, mas 2D (já referente à classe positiva) para
`HistGradientBoostingClassifier`/`XGBClassifier` — o formato varia por
modelo, tratado explicitamente em vez de assumido fixo.

## Registro de modelos (`registry.py`)

Cada modelo treinado (já calibrado) é salvo como um artefato `joblib` +
uma entrada num manifesto JSON (versão, símbolo, timeframe, estratégia,
métricas, flag `approved`). Rollback é apenas reapontar o ponteiro
`current` para uma versão anterior — nenhum artefato é apagado.
`approved` é sempre uma decisão manual (`--approve` na CLI), nunca
automática — ver critérios abaixo.

**Aviso de segurança:** `joblib.load` desserializa via pickle e não é
seguro contra artefatos de origem não confiável. Aceitável aqui porque o
registro só carrega artefatos produzidos internamente pelo próprio
pipeline de treino — nunca um upload externo.

## Critérios de aprovação de um modelo (prompt mestre, seção 12)

Avaliação sempre **manual** — a CLI nunca aprova um modelo sozinha:

1. Supera o baseline fora da amostra, depois de custos.
2. Probabilidades razoavelmente calibradas.
3. Ainda é útil (edge positivo) depois de custos reais.
4. Número de trades suficiente para conclusão estatística.
5. Estável entre períodos/regimes, não dependente de uma janela
   excepcional (isso exige walk-forward real — Fase 9).

## Comandos CLI

```bash
python -m app.cli ml build-dataset --symbol EURUSD --timeframe M1 --strategy ema_crossover \
    --max-horizon-bars 50 --out datasets/eurusd_m1_ema_crossover.csv

python -m app.cli ml train --dataset datasets/eurusd_m1_ema_crossover.csv \
    --symbol EURUSD --timeframe M1 --strategy-name ema_crossover_baseline \
    --model logistic_regression

python -m app.cli ml evaluate --version <versao_ou_omitir_para_current>

# Fase 9: multiplas janelas expansivas + veredito formal de aprovacao
python -m app.cli ml walk-forward --dataset datasets/eurusd_m1_ema_crossover.csv \
    --symbol EURUSD --model logistic_regression --n-windows 5
```

Ver `app/ml/walk_forward.py` (janelas expansivas com embargo, repetindo a
disciplina desta seção em cada janela) e `app/ml/approval.py` (os 5
critérios abaixo aplicados formalmente sobre o relatório de
walk-forward — sempre uma recomendação, nunca uma aprovação automática).

## Limitações e decisões conhecidas

- `ml train` continua usando uma única divisão treino/teste — `ml
  walk-forward` (Fase 9) existe como comando de diagnóstico separado,
  ainda sem integração automática no fluxo de registro (quem decide se
  o resultado do walk-forward justifica treinar/aprovar a versão final é
  o humano, não o pipeline).
- Walk-forward (Fase 9) testa estabilidade multi-período no MESMO
  símbolo; estabilidade multi-símbolo continua sem teste.
- O critério "supera o baseline" (`app/ml/approval.py`) é avaliado como
  "edge positivo depois de custos" (baseline implícito = 0, não operar)
  — uma comparação direta contra a mesma estratégia SEM o filtro de IA,
  nas mesmas janelas, exige a integração real de modelo-como-filtro
  (Fase 11+).
- `LightGBM` não foi instalado — opcional pelo prompt mestre, sem
  consumidor concreto ainda (mesmo raciocínio de `docs/assumptions.md`).
- Intervalo de confiança da expectativa usa aproximação normal, não
  bootstrap (suficiente para uma checagem de aprovação, não para um
  paper).
- Bug real encontrado pelos testes de integração da CLI: sinais gerados
  antes do aquecimento de `required_lookback_bars()` (200 barras)
  continham features `NaN` e quebravam o treino de regressão logística —
  corrigido pulando essas barras em `build_signal_dataset` (ver seção
  acima).
