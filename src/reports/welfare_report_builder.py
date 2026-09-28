"""ReportLab formatter for already-authorized welfare report DTOs."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .schemas import WelfareReportDTO


class WelfareReportBuilder:
    """Build a PDF without database, authorization, model, or RAG access."""

    def build(self, report: WelfareReportDTO) -> bytes:
        output = BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=A4,
            rightMargin=16 * mm,
            leftMargin=16 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title='SURAKSHAI Welfare Report',
            author='SURAKSHAI',
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name='ReportTitle', parent=styles['Title'], alignment=TA_CENTER, textColor=colors.HexColor('#17324D'), spaceAfter=8))
        styles.add(ParagraphStyle(name='Section', parent=styles['Heading2'], textColor=colors.HexColor('#17324D'), spaceBefore=10, spaceAfter=5))
        styles.add(ParagraphStyle(name='Small', parent=styles['BodyText'], fontSize=8.5, leading=11))
        story = [
            Paragraph('SURAKSHAI Welfare Officer Report', styles['ReportTitle']),
            Paragraph('CONFIDENTIAL', styles['Heading3']),
            Paragraph('Privacy-preserving decision-support export', styles['BodyText']),
            Spacer(1, 8),
        ]

        self._section(story, 'Report Information', styles)
        story.append(self._table([
            ['Report ID', report.metadata.report_id],
            ['Generated', report.metadata.generated_at],
            ['Reference date', report.metadata.reference_date],
            ['Coverage period', report.metadata.coverage_period],
            ['Model version', report.metadata.model_version],
            ['Data mode', report.metadata.data_mode],
        ], styles))

        self._section(story, 'Privacy-Safe Personnel Reference', styles)
        story.append(Paragraph(f"Pseudonymous personnel reference: <b>{report.personnel.pseudonymous_reference}</b>", styles['BodyText']))

        self._section(story, 'Risk Summary', styles)
        story.append(self._table([
            ['Current risk category', report.risk.risk_category],
            ['Model', report.risk.model],
            ['Data mode', report.risk.data_mode],
        ], styles))
        story.append(Paragraph(report.risk.disclaimer, styles['Small']))

        self._section(story, 'Risk Trend', styles)
        if report.trend.observations:
            story.append(self._table(
                [['Reference date', 'Risk category', 'Severity']] + [
                    [item.reference_date, item.risk_category, item.severity or 'Not recorded']
                    for item in report.trend.observations
                ], styles))
        else:
            story.append(Paragraph('Historical risk trend unavailable.', styles['BodyText']))
        story.append(Paragraph(f"7-day trend: {report.trend.trend_7d}<br/>30-day trend: {report.trend.trend_30d}<br/>Persistence: {report.trend.persistence}", styles['Small']))

        self._section(story, 'Key Contributing Factors', styles)
        if report.factors:
            story.append(self._table(
                [['Factor', 'Value', 'Direction', 'Magnitude', 'Explanation']] + [
                    [item.label, self._value(item.value), item.direction, f'{item.magnitude:.3f}', item.explanation]
                    for item in report.factors
                ], styles))
        else:
            story.append(Paragraph('No contributing factors were returned.', styles['BodyText']))
        story.append(Paragraph('SHAP contributions describe model influence and are not causal or clinical determinations.', styles['Small']))

        self._section(story, 'Operational Summary', styles)
        story.append(self._table([[key.replace('_', ' ').title(), self._value(value)] for key, value in report.operational_summary.items()] or [['Data', 'Unavailable']], styles))

        self._section(story, 'Voluntary Wellness Summary', styles)
        if report.wellness.available:
            story.append(self._table(
                [['Assessment date', report.wellness.assessment_date or 'Not recorded']] + [
                    [key.replace('_', ' ').title(), self._value(value)]
                    for key, value in report.wellness.values.items()
                ], styles))
        else:
            story.append(Paragraph(report.wellness.message, styles['BodyText']))

        self._section(story, 'Grounded Welfare Recommendations', styles)
        if report.recommendations:
            story.append(self._table(
                [['Category', 'Recommendation', 'Priority', 'Rationale', 'Sources']] + [
                    [item.category, item.action, item.priority or 'Not recorded', item.rationale, ', '.join(item.sources)]
                    for item in report.recommendations
                ], styles))
        else:
            story.append(Paragraph('No recommendation output was returned.', styles['BodyText']))

        self._section(story, 'Welfare Alerts', styles)
        if report.alerts:
            story.append(self._table(
                [['Alert reference', 'Severity', 'Created', 'Status', 'Acknowledged', 'Reviewed', 'Follow-up', 'Resolution']] + [
                    [item.alert_reference, item.severity or 'Not recorded', item.created_at or 'Not recorded', item.status or 'Not recorded', 'Yes' if item.acknowledged else 'No', 'Yes' if item.reviewed else 'No', item.follow_up or 'Not recorded', item.resolution or 'Not recorded']
                    for item in report.alerts
                ], styles))
        else:
            story.append(Paragraph('No welfare alerts were returned.', styles['BodyText']))

        self._section(story, 'Welfare Interventions', styles)
        if report.interventions:
            story.append(self._table(
                [['Intervention type', 'Created', 'Status', 'Scheduled follow-up', 'Outcome']] + [
                    [item.intervention_type or 'Not recorded', item.created_at or 'Not recorded', item.status or 'Not recorded', item.scheduled_follow_up or 'Not recorded', item.outcome_category or 'Not recorded']
                    for item in report.interventions
                ], styles))
        else:
            story.append(Paragraph('No welfare interventions were returned.', styles['BodyText']))

        self._section(story, 'Privacy and Safety Notice', styles)
        for notice in report.privacy_notice:
            story.append(Paragraph(notice, styles['Small']))
            story.append(Spacer(1, 3))

        document.build(story, onFirstPage=self._footer, onLaterPages=self._footer)
        return output.getvalue()

    @staticmethod
    def _section(story, title, styles):
        story.append(Paragraph(title, styles['Section']))

    @staticmethod
    def _value(value):
        return 'Unavailable' if value is None else str(value)

    @staticmethod
    def _table(rows, styles):
        table = Table([[Paragraph(str(cell), styles['Small']) for cell in row] for row in rows], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#DCE8F2')),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#9AA9B5')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return table

    @staticmethod
    def _footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#5B6770'))
        canvas.drawString(16 * mm, 9 * mm, 'CONFIDENTIAL - Authorized SURAKSHAI welfare use only')
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f'Page {document.page}')
        canvas.restoreState()
