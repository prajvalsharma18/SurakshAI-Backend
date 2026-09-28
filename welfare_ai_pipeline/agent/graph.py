from __future__ import annotations
from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph
from agent.nodes import load_analysis_node, prepare_context_node, recommendation_node, report_data_node, pdf_node

class GraphState(TypedDict, total=False):
    analysis_path: str
    combined_analysis: dict[str, Any]
    llm_context: str
    recommendations: dict[str, Any]
    report_data: dict[str, Any]
    pdf_path: str

def build_graph():
    b = StateGraph(GraphState)
    b.add_node("load_analysis", load_analysis_node)
    b.add_node("prepare_context", prepare_context_node)
    b.add_node("generate_recommendations", recommendation_node)
    b.add_node("build_report_data", report_data_node)
    b.add_node("generate_pdf", pdf_node)
    b.add_edge(START, "load_analysis")
    b.add_edge("load_analysis", "prepare_context")
    b.add_edge("prepare_context", "generate_recommendations")
    b.add_edge("generate_recommendations", "build_report_data")
    b.add_edge("build_report_data", "generate_pdf")
    b.add_edge("generate_pdf", END)
    return b.compile()

def run_graph(analysis_path: str):
    return build_graph().invoke({"analysis_path": str(analysis_path)})
