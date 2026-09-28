from __future__ import annotations
import json, os, re
from typing import Literal
from pydantic import BaseModel, Field

Priority = Literal["LOW", "MEDIUM", "HIGH"]

class RecommendationItem(BaseModel):
    category: str = Field(description="Welfare action category")
    priority: Priority
    recommendation: str
    rationale: str

class WelfareRecommendations(BaseModel):
    summary: str
    items: list[RecommendationItem]
    human_review_note: str

SYSTEM_PROMPT = """You are a personnel welfare decision-support assistant.
Interpret only the supplied structured analysis and propose non-disciplinary, human-reviewable welfare actions.
Rules:
- Do not diagnose mental or physical health conditions.
- Do not make promotion, posting, disciplinary, operational, or fitness decisions.
- Do not invent facts that are not present in the analysis.
- Phrase every recommendation as an action for an authorized welfare officer to review.
- Focus on workload review, rest/leave review, duty-pattern review, voluntary counseling/support discussion, or follow-up assessment.
- Be concise and evidence-grounded."""

def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM did not return a JSON object.")
    return json.loads(text[start:end + 1])

def build_demo_recommendations(analysis: dict) -> dict:
    risk = analysis["risk_assessment"]["risk_category"]
    anomaly = analysis["anomaly_analysis"]["anomaly_detected"]
    trends = analysis["trend_analysis"]["trends"]
    items = []
    if trends.get("workload_30d_avg", {}).get("direction") == "INCREASING":
        items.append({"category": "workload", "priority": "HIGH" if risk == "HIGH" else "MEDIUM", "recommendation": "Review current workload and recent duty allocation.", "rationale": "The supplied workload indicator is increasing."})
    if trends.get("current_consecutive_duty_days", {}).get("direction") == "INCREASING":
        items.append({"category": "rest_leave", "priority": "HIGH" if risk == "HIGH" else "MEDIUM", "recommendation": "Review consecutive duty periods and available rest or leave opportunities.", "rationale": "The supplied consecutive-duty indicator is increasing."})
    if trends.get("sleep_quality", {}).get("direction") == "DECREASING":
        items.append({"category": "wellness_follow_up", "priority": "MEDIUM", "recommendation": "Consider a voluntary welfare follow-up regarding reported rest and wellness.", "rationale": "The supplied sleep-quality indicator is declining."})
    if anomaly:
        items.append({"category": "pattern_review", "priority": "MEDIUM", "recommendation": "Review the unusual recent pattern with an authorized welfare officer.", "rationale": "The anomaly detector flagged the latest observation as unusual."})
    if not items:
        items.append({"category": "routine_follow_up", "priority": "LOW", "recommendation": "Continue routine welfare monitoring and periodic review.", "rationale": "No strong anomaly or directional trend was detected."})
    result = WelfareRecommendations(summary=f"Risk category is {risk}. The recommendations are decision-support suggestions for authorized human review.", items=items, human_review_note="Final welfare intervention remains a human decision; the system does not diagnose conditions.")
    output = result.model_dump(); output["mode"] = "DEMO_RULE_BASED"; return output

def generate_recommendations(analysis: dict) -> dict:
    provider = os.getenv("LLM_PROVIDER", "demo").strip().lower()
    if provider == "demo":
        return build_demo_recommendations(analysis)
    if provider == "mistral":
        if not os.getenv("MISTRAL_API_KEY"):
            raise RuntimeError("MISTRAL_API_KEY is not set.")
        from langchain_mistralai import ChatMistralAI
        llm = ChatMistralAI(model=os.getenv("MISTRAL_MODEL", "mistral-large-latest"), temperature=0, max_retries=2)
        prompt = SYSTEM_PROMPT + "\n\nReturn ONLY valid JSON matching this schema:\n" + json.dumps(WelfareRecommendations.model_json_schema(), indent=2) + "\n\nStructured analysis:\n" + json.dumps(analysis, indent=2)
        parsed = _extract_json(str(llm.invoke(prompt).content))
        output = WelfareRecommendations.model_validate(parsed).model_dump(); output["mode"] = "MISTRAL"; return output
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set.")
        model_name = os.getenv("OPENAI_MODEL")
        if not model_name:
            raise RuntimeError("Set OPENAI_MODEL in .env for OpenAI mode.")
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model_name, temperature=0, max_retries=2)
        structured_llm = llm.with_structured_output(WelfareRecommendations, method="json_schema")
        response = structured_llm.invoke([("system", SYSTEM_PROMPT), ("human", "Generate grounded welfare recommendations for this analysis:\n\n" + json.dumps(analysis, indent=2))])
        output = response.model_dump() if isinstance(response, WelfareRecommendations) else WelfareRecommendations.model_validate(response).model_dump(); output["mode"] = "OPENAI"; return output
    raise ValueError("LLM_PROVIDER must be demo, mistral, or openai.")
