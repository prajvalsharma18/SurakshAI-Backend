from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd


TrendDirection = Literal[
    "INCREASING",
    "DECREASING",
    "STABLE"
]


FEATURES = [
    "night_duty_7d_count",
    "workload_30d_avg",
    "current_consecutive_duty_days",
    "sleep_quality",
    "leave_days_30d",
]


def classify_trend(
    values: pd.Series,
    window_size: int = 7,
    threshold: float = 0.10
) -> tuple[TrendDirection, float, float, float]:

    values = pd.to_numeric(
        values,
        errors="coerce"
    ).dropna()

    if len(values) < 4:
        raise ValueError(
            "At least 4 observations are required."
        )

    # Make sure both windows can exist
    window_size = min(
        window_size,
        len(values) // 2
    )

    # Previous window
    previous_window = values.iloc[
        -2 * window_size : -window_size
    ]

    # Recent window
    recent_window = values.iloc[
        -window_size :
    ]

    previous_average = float(
        previous_window.mean()
    )

    recent_average = float(
        recent_window.mean()
    )

    # Percentage change
    if previous_average == 0:

        if recent_average > 0:
            change_percent = 100.0

        elif recent_average < 0:
            change_percent = -100.0

        else:
            change_percent = 0.0

    else:

        change_percent = (
            (recent_average - previous_average)
            / abs(previous_average)
        ) * 100

    # Decide trend
    if change_percent > threshold * 100:
        direction = "INCREASING"

    elif change_percent < -threshold * 100:
        direction = "DECREASING"

    else:
        direction = "STABLE"

    return (
        direction,
        round(change_percent, 2),
        round(previous_average, 2),
        round(recent_average, 2)
    )


def analyze_trends(
    history_df: pd.DataFrame,
    personnel_id: str
) -> dict:

    df = history_df.copy()

    # Required columns
    required_columns = {
        "personnel_id",
        "date"
    }

    missing_columns = (
        required_columns - set(df.columns)
    )

    if missing_columns:
        raise ValueError(
            f"Missing required columns: "
            f"{sorted(missing_columns)}"
        )

    # Convert date
    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    if df["date"].isna().any():
        raise ValueError(
            "Historical data contains invalid dates."
        )

    # Select requested personnel
    df = df[
        df["personnel_id"] == personnel_id
    ].sort_values("date")

    if df.empty:
        raise ValueError(
            f"No historical data found "
            f"for personnel {personnel_id}"
        )

    results = {}

    # Analyze each feature
    for feature in FEATURES:

        if feature not in df.columns:
            raise ValueError(
                f"Missing feature: {feature}"
            )

        (
            direction,
            change_percent,
            previous_average,
            recent_average
        ) = classify_trend(
            df[feature]
        )

        results[feature] = {
            "direction": direction,
            "change_percent": change_percent,
            "previous_average": previous_average,
            "recent_average": recent_average
        }

    return {
        "personnel_id": personnel_id,

        "start_date": (
            df["date"]
            .iloc[0]
            .date()
            .isoformat()
        ),

        "end_date": (
            df["date"]
            .iloc[-1]
            .date()
            .isoformat()
        ),

        "observations": len(df),

        "trends": results
    }


def load_history(
    csv_path: str | Path
) -> pd.DataFrame:

    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Historical data not found: {path}"
        )

    return pd.read_csv(path)