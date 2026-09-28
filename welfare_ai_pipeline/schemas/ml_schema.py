from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RiskCategory = Literal["LOW", "ELEVATED", "HIGH"]
RiskDirection = Literal["increases_risk", "reduces_risk"]


class RiskProbabilities(BaseModel):
    LOW: float = Field(ge=0.0, le=1.0)
    ELEVATED: float = Field(ge=0.0, le=1.0)
    HIGH: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def probabilities_must_sum_to_one(self):
        total = self.LOW + self.ELEVATED + self.HIGH
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Risk probabilities must sum to 1.0, got {total:.6f}")
        return self


class RiskPrediction(BaseModel):
    risk_category: RiskCategory
    risk_probabilities: RiskProbabilities
    model_version: str
    feature_version: str
    data_mode: str
    wellness_available: int = Field(ge=0, le=1)


class ContributingFactor(BaseModel):
    feature: str
    contribution: float
    direction: RiskDirection


class Explanation(BaseModel):
    top_contributing_factors: list[ContributingFactor]


class MLModelOutput(BaseModel):
    personnel_id: str
    reference_date: date
    risk_prediction: RiskPrediction
    explanation: Explanation
