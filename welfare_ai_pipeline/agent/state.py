from __future__ import annotations
from typing import Any, TypedDict

class WelfareState(TypedDict, total=False):
    analysis_path: str
    combined_analysis: dict[str, Any]
    llm_context: str
    recommendations: dict[str, Any]
    report_data: dict[str, Any]
    pdf_path: str
