from __future__ import annotations
import argparse, json
from pathlib import Path
from dotenv import load_dotenv
from agent.graph import run_graph

BASE_DIR=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description="Run the SurakshAI LangGraph welfare pipeline")
    parser.add_argument("--input", default=str(BASE_DIR/"data"/"combined_analysis.json"))
    args=parser.parse_args()
    load_dotenv(BASE_DIR/".env")
    result=run_graph(args.input)
    out=BASE_DIR/"data"/"welfare_recommendations.json"
    out.write_text(json.dumps(result["recommendations"],indent=2),encoding="utf-8")
    print("="*70)
    print("SURAKSHAI LANGGRAPH WELFARE PIPELINE")
    print("="*70)
    print(f"Personnel ID : {result['combined_analysis']['personnel_id']}")
    print(f"Risk         : {result['combined_analysis']['risk_assessment']['risk_category']}")
    print(f"LLM mode     : {result['recommendations'].get('mode')}")
    print("\nRecommendations:")
    for item in result["recommendations"]["items"]:
        print(f"- [{item['priority']}] {item['category']}: {item['recommendation']}")
    print(f"\nRecommendation JSON: {out}")
    print(f"PDF: {result['pdf_path']}")

if __name__=="__main__":
    main()
