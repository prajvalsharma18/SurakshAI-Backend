from __future__ import annotations
from pathlib import Path
from typing import Any
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "reports" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def generate_pdf(report_data: dict[str, Any]) -> Path:
    pid = report_data["personnel_id"]
    out = OUTPUT_DIR / f"welfare_report_{pid}.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=16*mm, bottomMargin=16*mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleCustom", parent=styles["Title"], alignment=TA_CENTER, fontSize=17, spaceAfter=10)
    heading = ParagraphStyle("HeadingCustom", parent=styles["Heading2"], fontSize=12, spaceBefore=9, spaceAfter=5)
    body = ParagraphStyle("BodyCustom", parent=styles["BodyText"], fontSize=9.3, leading=12)
    small = ParagraphStyle("SmallCustom", parent=styles["BodyText"], fontSize=8, leading=10, textColor=colors.grey)
    story = [Paragraph("PERSONNEL WELFARE ASSESSMENT REPORT", title)]
    story.append(Paragraph(f"<b>Personnel ID:</b> {pid}<br/><b>Reference Date:</b> {report_data['reference_date']}", body))

    risk = report_data["risk_assessment"]
    story.append(Paragraph("1. Risk Assessment", heading))
    t = Table([["Risk Category", risk["risk_category"]], ["LOW Probability", f"{risk['risk_probabilities']['LOW']:.1%}"], ["ELEVATED Probability", f"{risk['risk_probabilities']['ELEVATED']:.1%}"], ["HIGH Probability", f"{risk['risk_probabilities']['HIGH']:.1%}"], ["Model Version", risk.get("model_version", "N/A")]], colWidths=[55*mm,110*mm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#EAF2F8")),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#B8C7D9")),("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9)]))
    story.append(t)

    story.append(Paragraph("2. Contributing Factors", heading))
    rows = [["Feature","Contribution","Direction"]] + [[x["feature"],f"{x['contribution']:.2f}",x["direction"]] for x in report_data.get("contributing_factors", [])]
    t = Table(rows,colWidths=[85*mm,30*mm,50*mm],repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F4E79")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#C5D1DC")),("FONTSIZE",(0,0),(-1,-1),8.3)]))
    story.append(t)

    a=report_data["anomaly_analysis"]
    story.append(Paragraph("3. Anomaly Analysis", heading))
    story.append(Paragraph(f"<b>Status:</b> {a.get('anomaly_label','N/A')}<br/><b>Detected:</b> {a.get('anomaly_detected',False)}<br/><b>Score:</b> {a.get('anomaly_score','N/A')}", body))

    story.append(Paragraph("4. Trend Analysis", heading))
    rows=[["Indicator","Direction","Change"]]+[[k,v["direction"],f"{v['change_percent']:.2f}%"] for k,v in report_data["trend_analysis"]["trends"].items()]
    t=Table(rows,colWidths=[95*mm,40*mm,30*mm],repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F4E79")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#C5D1DC")),("FONTSIZE",(0,0),(-1,-1),8.3)]))
    story.append(t)

    r=report_data["recommendations"]
    story.append(Paragraph("5. Welfare Recommendations", heading))
    story.append(Paragraph(f"<b>Summary:</b> {r['summary']}", body))
    rows=[["Category","Priority","Recommendation","Rationale"]]+[[i["category"],i["priority"],i["recommendation"],i["rationale"]] for i in r["items"]]
    t=Table(rows,colWidths=[30*mm,22*mm,58*mm,55*mm],repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F4E79")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#C5D1DC")),("FONTSIZE",(0,0),(-1,-1),7.4)]))
    story.append(t)
    story.append(Spacer(1,8))
    story.append(Paragraph(r["human_review_note"], small))
    story.append(Paragraph("This report is a welfare decision-support artifact based on supplied analytical signals. It is not a medical diagnosis.", small))
    doc.build(story)
    return out
