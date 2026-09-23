import os
import time
from xml.sax.saxutils import escape
from typing import Dict, Optional
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


class ExportService:
    """
    Export Service generating:
    - Official DOCX Meeting Protocols for Samruk-Kazyna Ondeu
    - Official PDF Meeting Protocols with corporate layout and typography
    """

    def __init__(self, output_dir: str = "storage/protocols"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._init_pdf_fonts()

    def _init_pdf_fonts(self):
        """Register Arial / Helvetica fonts for Cyrillic / Kazakh support in ReportLab."""
        try:
            # Look for Windows system Arial font
            font_path = "C:\\Windows\\Fonts\\arial.ttf"
            font_bold_path = "C:\\Windows\\Fonts\\arialbd.ttf"
            if os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont("Arial", font_path))
                pdfmetrics.registerFont(TTFont("Arial-Bold", font_bold_path))
                self.pdf_font = "Arial"
                self.pdf_font_bold = "Arial-Bold"
                pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial", boldItalic="Arial-Bold")
            else:
                self.pdf_font = "Helvetica"
                self.pdf_font_bold = "Helvetica-Bold"
        except Exception:
            self.pdf_font = "Helvetica"
            self.pdf_font_bold = "Helvetica-Bold"

    def export_to_docx(self, protocol_data: Dict, filename: Optional[str] = None) -> str:
        """
        Generate a corporate Word (.docx) protocol document.
        """
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"Протокол_совещания_{timestamp}.docx"
        output_path = os.path.join(self.output_dir, filename)

        doc = Document()

        # Set page margins (2 cm)
        sections = doc.sections
        for section in sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)

        # Corporate Header
        company_name = protocol_data.get("company") or os.getenv("DEFAULT_COMPANY", "Организация")
        p_corp = doc.add_paragraph()
        p_corp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_corp = p_corp.add_run(company_name.upper())
        run_corp.bold = True
        run_corp.font.size = Pt(14)
        run_corp.font.color.rgb = RGBColor(16, 75, 140)  # Corporate deep blue

        # Protocol Title
        p_type = doc.add_paragraph()
        p_type.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_type = p_type.add_run("ПРОТОКОЛ СОВЕЩАНИЯ")
        run_type.bold = True
        run_type.font.size = Pt(16)

        # Topic & Date
        p_meta = doc.add_paragraph()
        p_meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title = protocol_data.get("title", "Оперативное совещание")
        date_str = protocol_data.get("date", time.strftime("%d.%m.%Y"))
        run_meta = p_meta.add_run(f"Тема: {title}\nДата: {date_str}")
        run_meta.italic = True
        run_meta.font.size = Pt(11)

        # Divider line
        p_div = doc.add_paragraph()
        p_div.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_div.add_run("―" * 50)

        # Agenda
        p_ag_title = doc.add_paragraph()
        r = p_ag_title.add_run("ПОВЕСТКА ДНЯ:")
        r.bold = True
        r.font.size = Pt(12)

        agenda = protocol_data.get("agenda", [])
        if agenda:
            for i, item in enumerate(agenda, 1):
                p_ag = doc.add_paragraph(f"{i}. {item}", style='List Number')
                p_ag.paragraph_format.left_indent = Inches(0.25)
        else:
            doc.add_paragraph("Повестка отдельно не указана в стенограмме.")

        # Participants
        p_part_title = doc.add_paragraph()
        r_part = p_part_title.add_run("ПРИСУТСТВОВАЛИ:")
        r_part.bold = True
        r_part.font.size = Pt(12)

        participants = protocol_data.get("participants", [])
        if participants:
            for p in participants:
                name = p.get("name", "")
                role = p.get("role", "")
                dept = p.get("department", "")
                p_p = doc.add_paragraph(f"• {name} — {role}" + (f" ({dept})" if dept else ""))
                p_p.paragraph_format.left_indent = Inches(0.25)

        # Executive Summary
        p_sum_title = doc.add_paragraph()
        r_sum = p_sum_title.add_run("КРАТКОЕ РЕЗЮМЕ И ДОКЛАДЫ:")
        r_sum.bold = True
        r_sum.font.size = Pt(12)

        summary_blocks = protocol_data.get("summary", [])
        for block in summary_blocks:
            p_topic = doc.add_paragraph()
            r_top = p_topic.add_run(f"Раздел: {block.get('topic', 'Тема')}")
            r_top.bold = True
            r_top.font.color.rgb = RGBColor(40, 40, 40)

            key_points = block.get("key_points", [])
            for kp in key_points:
                p_kp = doc.add_paragraph(f"– {kp}")
                p_kp.paragraph_format.left_indent = Inches(0.25)

            risks = block.get("risks", [])
            if risks:
                p_r = doc.add_paragraph()
                p_r.add_run("Риски и блокеры: ").bold = True
                p_r.add_run("; ".join(risks))
                p_r.paragraph_format.left_indent = Inches(0.25)

        # Action Items Table
        p_task_title = doc.add_paragraph()
        r_t = p_task_title.add_run("ПОРУЧЕНИЯ ПО ИТОГАМ СОВЕЩАНИЯ:")
        r_t.bold = True
        r_t.font.size = Pt(13)
        r_t.font.color.rgb = RGBColor(16, 75, 140)

        tasks = protocol_data.get("tasks", [])
        table = doc.add_table(rows=1, cols=6)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False

        # Header Row
        headers = ["№", "Суть поручения", "Ответственный", "Срок", "Приоритет", "Статус"]
        hdr_cells = table.rows[0].cells
        for i, text in enumerate(headers):
            hdr_cells[i].text = text
            # Format header styling
            shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="104B8C"/>')
            hdr_cells[i]._tc.get_or_add_tcPr().append(shd)
            for p in hdr_cells[i].paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in p.runs:
                    run.font.bold = True
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(255, 255, 255)

        # Set column widths
        widths = [Inches(0.4), Inches(2.8), Inches(1.5), Inches(1.0), Inches(0.9), Inches(0.9)]
        for row in table.rows:
            for idx, width in enumerate(widths):
                row.cells[idx].width = width

        for t in tasks:
            row_cells = table.add_row().cells
            row_cells[0].text = str(t.get("id", ""))
            row_cells[1].text = str(t.get("task", ""))
            row_cells[2].text = str(t.get("assignee", ""))
            row_cells[3].text = str(t.get("deadline", ""))
            row_cells[4].text = str(t.get("priority", "Обычный"))
            row_cells[5].text = str(t.get("status", "В работе"))

            # Alignments
            row_cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            row_cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            row_cells[4].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            row_cells[5].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

            for c in row_cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9.5)

        for warning in protocol_data.get("warnings", []):
            doc.add_paragraph(warning)
        doc.add_heading("СТЕНОГРАММА", level=1)
        for item in protocol_data.get("dialogue", []):
            doc.add_paragraph(f"[{item.get('timestamp', '')}] {item.get('speaker', '')}: {item.get('text', '')}")

        # Signatures
        doc.add_paragraph().paragraph_format.space_before = Pt(20)
        p_sig = doc.add_paragraph()
        leader = protocol_data.get("leader", "Председатель совещания")
        p_sig.add_run(f"Председатель совещания: ____________________ / {leader} /\n\n")
        p_sig.add_run("Секретарь совещания:     ____________________ / Ответственный секретарь /")

        doc.save(output_path)
        print(f"[Export] DOCX protocol saved to: {output_path}")
        return output_path

    def export_to_txt(self, protocol_data: Dict, filename: str) -> str:
        output_path = os.path.join(self.output_dir, filename)
        with open(output_path, "w", encoding="utf-8-sig") as stream:
            stream.write(protocol_data.get("title", "Стенограмма") + "\n\n")
            for item in protocol_data.get("dialogue", []):
                stream.write(f"[{item.get('timestamp', '')}] {item.get('speaker', '')}: {item.get('text', '')}\n\n")
            if not protocol_data.get("dialogue"):
                stream.write(protocol_data.get("transcript", "Речь не обнаружена."))
        return output_path

    def export_to_pdf(self, protocol_data: Dict, filename: Optional[str] = None) -> str:
        """
        Generate an official PDF meeting protocol.
        """
        # Transcript text is data, never ReportLab XML/HTML markup.
        def safe(value):
            if isinstance(value, str):
                return escape(value)
            if isinstance(value, list):
                return [safe(item) for item in value]
            if isinstance(value, dict):
                return {key: safe(item) for key, item in value.items()}
            return value
        protocol_data = safe(protocol_data)
        if not filename:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"Протокол_совещания_{timestamp}.pdf"
        output_path = os.path.join(self.output_dir, filename)

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        styles = getSampleStyleSheet()
        normal = styles["Normal"]

        corp_style = ParagraphStyle(
            "CorpHeader",
            parent=normal,
            fontName=self.pdf_font_bold,
            fontSize=13,
            leading=16,
            alignment=1,
            textColor=colors.HexColor("#104B8C")
        )

        title_style = ParagraphStyle(
            "DocTitle",
            parent=normal,
            fontName=self.pdf_font_bold,
            fontSize=15,
            leading=18,
            alignment=1,
            textColor=colors.HexColor("#1A1A1A")
        )

        meta_style = ParagraphStyle(
            "MetaStyle",
            parent=normal,
            fontName=self.pdf_font,
            fontSize=10,
            leading=13,
            alignment=1,
            textColor=colors.HexColor("#555555")
        )

        h2_style = ParagraphStyle(
            "Heading2Custom",
            parent=normal,
            fontName=self.pdf_font_bold,
            fontSize=11,
            leading=15,
            textColor=colors.HexColor("#104B8C")
        )

        body_style = ParagraphStyle(
            "BodyCustom",
            parent=normal,
            fontName=self.pdf_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#222222")
        )

        table_header_style = ParagraphStyle(
            "THStyle",
            parent=normal,
            fontName=self.pdf_font_bold,
            fontSize=8.5,
            leading=10,
            alignment=1,
            textColor=colors.white
        )

        table_cell_style = ParagraphStyle(
            "TCStyle",
            parent=normal,
            fontName=self.pdf_font,
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#111111")
        )

        story = []

        # Header
        company = protocol_data.get("company") or os.getenv("DEFAULT_COMPANY", "Организация")
        story.append(Paragraph(company.upper(), corp_style))
        story.append(Spacer(1, 4))
        story.append(Paragraph("ПРОТОКОЛ СОВЕЩАНИЯ", title_style))
        story.append(Spacer(1, 4))

        title = protocol_data.get("title", "Оперативное совещание")
        date_str = protocol_data.get("date", time.strftime("%d.%m.%Y"))
        story.append(Paragraph(f"Тема: {title}<br/>Дата: {date_str}", meta_style))
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CCCCCC")))
        story.append(Spacer(1, 10))

        # Agenda
        story.append(Paragraph("ПОВЕСТКА ДНЯ:", h2_style))
        agenda = protocol_data.get("agenda", [])
        for i, item in enumerate(agenda, 1):
            story.append(Paragraph(f"{i}. {item}", body_style))
        story.append(Spacer(1, 10))

        # Participants
        story.append(Paragraph("ПРИСУТСТВОВАЛИ:", h2_style))
        participants = protocol_data.get("participants", [])
        part_strs = []
        for p in participants:
            name = p.get("name", "")
            role = p.get("role", "")
            dept = p.get("department", "")
            part_strs.append(f"• {name} — {role}" + (f" ({dept})" if dept else ""))
        story.append(Paragraph("<br/>".join(part_strs), body_style))
        story.append(Spacer(1, 12))

        # Action Items Table
        story.append(Paragraph("РЕЕСТР ПОРУЧЕНИЙ:", h2_style))
        story.append(Spacer(1, 4))

        tasks = protocol_data.get("tasks", [])
        table_data = [
            [
                Paragraph("№", table_header_style),
                Paragraph("Суть поручения", table_header_style),
                Paragraph("Ответственный", table_header_style),
                Paragraph("Срок", table_header_style),
                Paragraph("Приоритет", table_header_style),
                Paragraph("Статус", table_header_style)
            ]
        ]

        for t in tasks:
            table_data.append([
                Paragraph(str(t.get("id", "")), table_cell_style),
                Paragraph(str(t.get("task", "")), table_cell_style),
                Paragraph(str(t.get("assignee", "")), table_cell_style),
                Paragraph(str(t.get("deadline", "")), table_cell_style),
                Paragraph(str(t.get("priority", "Обычный")), table_cell_style),
                Paragraph(str(t.get("status", "В работе")), table_cell_style)
            ])

        col_widths = [doc.width * ratio for ratio in (0.04, 0.34, 0.18, 0.16, 0.13, 0.15)]
        t_flowable = Table(table_data, colWidths=col_widths, repeatRows=1, splitInRow=1)
        t_flowable.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#104B8C')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CCCCCC')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(t_flowable)
        story.append(Spacer(1, 16))

        # Signatures
        leader = protocol_data.get("leader", "Председатель совещания")
        story.append(Paragraph(f"<b>Председатель совещания:</b> ____________________ / {leader} /", body_style))
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Секретарь совещания:</b>     ____________________ / Ответственный секретарь /", body_style))

        story.append(Spacer(1, 12))
        story.append(Paragraph("КРАТКОЕ РЕЗЮМЕ:", h2_style))
        for block in protocol_data.get("summary", []):
            for point in block.get("key_points", []):
                story.append(Paragraph(point, body_style))
                story.append(Spacer(1, 4))
        for warning in protocol_data.get("warnings", []):
            story.append(Paragraph(warning, body_style))
        story.append(Spacer(1, 12))
        story.append(Paragraph("СТЕНОГРАММА:", h2_style))
        for item in protocol_data.get("dialogue", []):
            story.append(Paragraph(f"[{item.get('timestamp', '')}] {item.get('speaker', '')}: {item.get('text', '')}", body_style))
            story.append(Spacer(1, 5))
        doc.build(story)
        print(f"[Export] PDF protocol saved to: {output_path}")
        return output_path
