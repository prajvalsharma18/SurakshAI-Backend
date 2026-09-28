from pathlib import Path

import pandas as pd
from sklearn.ensemble import IsolationForest

FEATURES = [
    "night_duty_7d_count",
    "workload_30d_avg",
    "current_consecutive_duty_days",
    "sleep_quality",
    "leave_days_30d",
]


def load_history(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Historical data file not found: {path}")
    return pd.read_csv(path)


def detect_current_anomaly(
    history_df: pd.DataFrame,
    personnel_id: str,
    contamination: float = 0.10,
) -> dict:
    """Prototype: compare the latest observation against prior observations."""
    df = history_df.copy()
    if "date" not in df.columns:
        raise ValueError("Historical data must contain a 'date' column.")

    df["date"] = pd.to_datetime(df["date"])
    df = df[df["personnel_id"] == personnel_id].sort_values("date")

    if len(df) < 10:
        raise ValueError("At least 10 historical observations are recommended.")

    missing = [feature for feature in FEATURES if feature not in df.columns]
    if missing:
        raise ValueError(f"Missing anomaly features: {missing}")

    baseline = df.iloc[:-1]
    current = df.iloc[-1]

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=42,
    )
    model.fit(baseline[FEATURES])

    current_row = current[FEATURES].to_frame().T
    prediction = int(model.predict(current_row)[0])
    decision_score = float(model.decision_function(current_row)[0])

    return {
        "personnel_id": personnel_id,
        "reference_date": current["date"].date().isoformat(),
        "anomaly_detected": prediction == -1,
        "anomaly_label": "ANOMALY" if prediction == -1 else "NORMAL",
        "anomaly_score": round(decision_score, 4),
    }
