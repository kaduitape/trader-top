"""Pipeline de machine learning (Fase 8).

O primeiro problema de ML deste projeto, conforme o prompt mestre (secao
12), NAO e prever preco futuro — e: "Dado um sinal valido da estrategia,
qual e a probabilidade de o alvo ser atingido antes do stop dentro de um
horizonte definido?". Todo o pipeline (`labels.py`, `datasets.py`,
`splits.py`, `train.py`, `calibration.py`, `validation.py`,
`explainability.py`, `registry.py`) existe para responder exatamente essa
pergunta, nunca para prever preco diretamente.

Modelos desta fase: regra sem IA (baseline), regressao logistica, Random
Forest, HistGradientBoosting, XGBoost — nenhum LSTM/Transformer/RL (a
prompt mestre so autoriza considerar esses a partir da Fase 14, e apenas se
os modelos tabulares mostrarem limitacoes reais)."""
