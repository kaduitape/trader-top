import numpy as np
import pandas as pd

from app.ml.datasets import ML_CATEGORICAL_FEATURE_COLUMNS, ML_NUMERIC_FEATURE_COLUMNS
from app.ml.preprocessing import build_preprocessor, feature_matrix


def _synthetic_dataframe(n: int = 20) -> pd.DataFrame:
    rng = np.random.RandomState(0)
    data = {column: rng.rand(n) for column in ML_NUMERIC_FEATURE_COLUMNS}
    data["session"] = ["LONDON" if i % 2 == 0 else "NEW_YORK" for i in range(n)]
    data["label"] = [i % 2 for i in range(n)]
    return pd.DataFrame(data)


def test_build_preprocessor_scales_numeric_when_requested() -> None:
    dataset = _synthetic_dataframe()
    preprocessor = build_preprocessor(scale_numeric=True)
    transformed = preprocessor.fit_transform(dataset)

    n_numeric = len(ML_NUMERIC_FEATURE_COLUMNS)
    numeric_part = np.asarray(transformed)[:, :n_numeric]
    # StandardScaler: media ~0, desvio padrao ~1 por coluna.
    assert np.allclose(numeric_part.mean(axis=0), 0.0, atol=1e-8)
    assert np.allclose(numeric_part.std(axis=0), 1.0, atol=1e-8)


def test_build_preprocessor_passthrough_when_scale_disabled() -> None:
    dataset = _synthetic_dataframe()
    preprocessor = build_preprocessor(scale_numeric=False)
    transformed = np.asarray(preprocessor.fit_transform(dataset))

    n_numeric = len(ML_NUMERIC_FEATURE_COLUMNS)
    numeric_part = transformed[:, :n_numeric]
    expected = dataset[list(ML_NUMERIC_FEATURE_COLUMNS)].to_numpy()
    assert np.allclose(numeric_part, expected)


def test_build_preprocessor_one_hot_encodes_session() -> None:
    dataset = _synthetic_dataframe()
    preprocessor = build_preprocessor(scale_numeric=False)
    transformed = np.asarray(preprocessor.fit_transform(dataset))

    n_numeric = len(ML_NUMERIC_FEATURE_COLUMNS)
    categorical_part = transformed[:, n_numeric:]
    # Duas sessoes distintas -> 2 colunas one-hot, cada linha soma exatamente 1.
    assert categorical_part.shape[1] == 2
    assert np.allclose(categorical_part.sum(axis=1), 1.0)


def test_build_preprocessor_handles_unknown_category_at_transform_time() -> None:
    dataset = _synthetic_dataframe()
    preprocessor = build_preprocessor(scale_numeric=False)
    preprocessor.fit(dataset)

    unseen = dataset.copy()
    unseen["session"] = "TOKYO"
    # handle_unknown="ignore": nao deve levantar excecao, apenas zera a linha one-hot.
    transformed = np.asarray(preprocessor.transform(unseen))
    n_numeric = len(ML_NUMERIC_FEATURE_COLUMNS)
    assert np.allclose(transformed[:, n_numeric:].sum(axis=1), 0.0)


def test_feature_matrix_selects_only_feature_columns() -> None:
    dataset = _synthetic_dataframe()
    matrix = feature_matrix(dataset)
    assert list(matrix.columns) == list(ML_NUMERIC_FEATURE_COLUMNS) + list(
        ML_CATEGORICAL_FEATURE_COLUMNS
    )
    assert "label" not in matrix.columns
