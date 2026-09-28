"""Typed response shapes for persisted risk-history APIs."""

from typing import TypedDict


class RiskHistoryItem(TypedDict, total=False):
    reference_date: str
    risk_category: str
    model_version: str
    data_mode: str
    probabilities: dict[str, float]
    created_at: str


class RiskHistoryResponse(TypedDict):
    items: list[RiskHistoryItem]
    page: int
    page_size: int
    total: int


class RiskSummaryResponse(TypedDict):
    total_personnel: int
    risk_distribution: dict[str, int]
    latest_prediction_date: str | None
