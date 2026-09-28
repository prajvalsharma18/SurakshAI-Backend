from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from llm.recommendations import generate_recommendations

def load_analysis_node(state: dict[str, Any]) -> dict[str, Any]:
    path = Path(state["analysis_path"])
    if not path.exists(): raise FileNotFoundError(f"Combined analysis not found: {path}")
    return {"combined_analysis": json.loads(path.read_text(encoding="utf-8"))}

def prepare_context_node(state: dict[str, Any]) -> dict[str, Any]:
    a = state["combined_analysis"]
    context = {"personnel_id": a.get("personnel_id"), "reference_date": a.get("reference_date"), "risk_assessment": a.get("risk_assessment"), "contributing_factors": a.get("contributing_factors", []), "anomaly_analysis": a.get("anomaly_analysis", {}), "trend_analysis": a.get("trend_analysis", {})}
    return {"llm_context": json.dumps(context, indent=2)}

def recommendation_node(state: dict[str, Any]) -> dict[str, Any]:
    return {"recommendations": generate_recommendations(state["combined_analysis"])}

def report_data_node(state: dict[str, Any]) -> dict[str, Any]:
    a = state["combined_analysis"]
    return {"report_data": {"personnel_id": a["personnel_id"], "reference_date": a["reference_date"], "risk_assessment": a["risk_assessment"], "contributing_factors": a.get("contributing_factors", []), "anomaly_analysis": a.get("anomaly_analysis", {}), "trend_analysis": a.get("trend_analysis", {}), "recommendations": state["recommendations"]}}

def pdf_node(state: dict[str, Any]) -> dict[str, Any]:
    from reports.pdf_generator import generate_pdf
    return {"pdf_path": str(generate_pdf(state["report_data"]))}
