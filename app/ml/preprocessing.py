"""Pre-processamento: selecao de features e transformacao.

Qualquer transformacao com estado (ex.: `StandardScaler` para regressao
logistica, `OneHotEncoder` para a sessao) e encapsulada num
`sklearn.compose.ColumnTransformer` dentro do `Pipeline` (ver
`app.ml.train`) — ela e ajustada (`fit`) SOMENTE no conjunto de treino
quando o `Pipeline.fit(X_train, ...)` roda, nunca no dataset inteiro.
Isso e o que impede vazamento de estatisticas do teste para o treino.
"""

from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.ml.datasets import ML_CATEGORICAL_FEATURE_COLUMNS, ML_NUMERIC_FEATURE_COLUMNS


def build_preprocessor(*, scale_numeric: bool) -> ColumnTransformer:
    """`scale_numeric=True` para modelos lineares (regressao logistica);
    `False` para modelos em arvore (Random Forest, HistGradientBoosting,
    XGBoost), que nao precisam nem se beneficiam de escala fixa."""
    numeric_transformer = StandardScaler() if scale_numeric else "passthrough"
    return ColumnTransformer(
        transformers=[
            ("numeric", numeric_transformer, list(ML_NUMERIC_FEATURE_COLUMNS)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(ML_CATEGORICAL_FEATURE_COLUMNS),
            ),
        ]
    )


def feature_matrix(dataset: object) -> object:
    """Seleciona apenas as colunas de feature (numericas + categoricas),
    na ordem esperada pelo `ColumnTransformer` — o alvo (`label`) e demais
    metadados ficam de fora."""
    import pandas as pd

    assert isinstance(dataset, pd.DataFrame)
    return dataset[list(ML_NUMERIC_FEATURE_COLUMNS) + list(ML_CATEGORICAL_FEATURE_COLUMNS)]
