"""
Breakout probability model. Deliberately a simple, well-calibrated
classifier (gradient boosted trees) rather than anything exotic -
with this little labeled data per symbol, calibration matters far
more than model complexity. Output is a probability, always paired
with the underlying feature values so it's explainable, never a bare
"buy" signal.
"""
from __future__ import annotations
import pandas as pd
import joblib
from pathlib import Path

from ai.features import FEATURE_COLUMNS, make_training_row
import config


class BreakoutModel:
    def __init__(self):
        self.model = None
        self.calibrator = None

    def train(self, bars_by_symbol: dict[str, pd.DataFrame], benchmark_df: pd.DataFrame):
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.calibration import CalibratedClassifierCV

        rows = []
        for symbol, df in bars_by_symbol.items():
            for i in range(60, len(df) - config.BREAKOUT_HORIZON_DAYS):
                row = make_training_row(
                    df, benchmark_df, i,
                    config.BREAKOUT_HORIZON_DAYS, config.BREAKOUT_LABEL_MOVE_PCT,
                )
                if row:
                    rows.append(row)

        if len(rows) < 200:
            raise ValueError(f"Only {len(rows)} training rows - need more history/symbols before training.")

        data = pd.DataFrame(rows)
        X, y = data[FEATURE_COLUMNS], data["label"]

        base = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.05)
        self.model = CalibratedClassifierCV(base, method="isotonic", cv=5)
        self.model.fit(X, y)

        print(f"Trained on {len(data)} rows. Positive rate: {y.mean():.2%}")

    def predict_proba(self, features: dict) -> float:
        if self.model is None:
            raise RuntimeError("Model not trained/loaded")
        X = pd.DataFrame([features])[FEATURE_COLUMNS]
        return float(self.model.predict_proba(X)[0][1])

    def save(self, path: str | None = None):
        path = path or config.MODEL_PATH
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, path)

    def load(self, path: str | None = None):
        path = path or config.MODEL_PATH
        self.model = joblib.load(path)
