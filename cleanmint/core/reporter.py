"""
core/reporter.py — CleanMint Report Exporter

Exports scan results and clean summaries to TXT, CSV, and PDF.
Uses only reportlab for PDF (pure Python, no system deps).
"""

import csv
from pathlib import Path
from datetime import datetime
from typing import Union

from core.scanner import ScanCategory, _human_size
from core.cleaner import CleanResult


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── TXT ───────────────────────────────────────────────────────

def export_txt(
    path: Union[str, Path],
    categories: list[ScanCategory] | None = None,
    results: list[CleanResult] | None = None,
    title: str = "CleanMint Report",
) -> Path:
    path = Path(path)
    lines = [
        "=" * 60,
        f"  {title}",
        f"  Generated: {_timestamp()}",
        "=" * 60,
        "",
    ]

    if categories:
        total = sum(c.size_bytes for c in categories)
        lines += [
            "SCAN RESULTS",
            "-" * 40,
            f"Total junk found: {_human_size(total)}",
            f"Categories scanned: {len(categories)}",
            "",
        ]
        for cat in sorted(categories, key=lambda c: c.size_bytes, reverse=True):
            flag = "✓ Recommended" if cat.recommended else "  Optional"
            lines.append(
                f"  [{cat.risk.upper():6}] {cat.name:<30} {cat.size_human:>10}   {flag}"
            )
        lines.append("")

    if results:
        total_freed = sum(r.freed_bytes for r in results)
        total_deleted = sum(r.deleted_count for r in results)
        mode = "DRY RUN" if (results and results[0].dry_run) else "ACTUAL CLEAN"
        lines += [
            f"CLEAN RESULTS ({mode})",
            "-" * 40,
            f"Total freed: {_human_size(total_freed)}",
            f"Items removed: {total_deleted}",
            "",
        ]
        for r in results:
            lines.append(f"  {r.category_name}: {r.freed_human} freed, {r.deleted_count} items")
            for err in r.errors:
                lines.append(f"    ERROR: {err}")
        lines.append("")

    lines += ["=" * 60, "End of report", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── CSV ───────────────────────────────────────────────────────

def export_csv(
    path: Union[str, Path],
    categories: list[ScanCategory] | None = None,
    results: list[CleanResult] | None = None,
) -> Path:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if categories:
            writer.writerow(["--- SCAN RESULTS ---"])
            writer.writerow(["Category", "Risk", "Size (bytes)", "Size (human)",
                             "Files", "Recommended", "Error"])
            for cat in categories:
                writer.writerow([
                    cat.name, cat.risk, cat.size_bytes, cat.size_human,
                    cat.file_count, cat.recommended, cat.error,
                ])
            writer.writerow([])

        if results:
            writer.writerow(["--- CLEAN RESULTS ---"])
            writer.writerow(["Category", "Dry Run", "Freed (bytes)", "Freed (human)",
                             "Deleted", "Skipped", "Errors"])
            for r in results:
                writer.writerow([
                    r.category_name, r.dry_run, r.freed_bytes, r.freed_human,
                    r.deleted_count, r.skipped_count, "; ".join(r.errors),
                ])

        writer.writerow([])
        writer.writerow(["Generated", _timestamp()])
    return path


# ── PDF ───────────────────────────────────────────────────────

def export_pdf(
    path: Union[str, Path],
    categories: list[ScanCategory] | None = None,
    results: list[CleanResult] | None = None,
    title: str = "CleanMint Report",
) -> Path:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        )
    except ImportError:
        raise ImportError("reportlab is required for PDF export: pip install reportlab")

    path = Path(path)
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()
    accent = colors.HexColor("#3ddc84")
    dark   = colors.HexColor("#1a1f2e")
    muted  = colors.HexColor("#6b7694")

    h1 = ParagraphStyle("H1", parent=styles["Heading1"],
                         textColor=dark, fontSize=18, spaceAfter=4)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"],
                         textColor=accent, fontSize=13, spaceAfter=4, spaceBefore=12)
    normal = ParagraphStyle("Normal", parent=styles["Normal"], fontSize=10, spaceAfter=2)
    small  = ParagraphStyle("Small",  parent=styles["Normal"],
                             fontSize=9, textColor=muted)

    story = [
        Paragraph(title, h1),
        Paragraph(f"Generated: {_timestamp()}", small),
        Spacer(1, 0.4*cm),
        HRFlowable(width="100%", thickness=1, color=accent),
        Spacer(1, 0.4*cm),
    ]

    if categories:
        total = sum(c.size_bytes for c in categories)
        story += [
            Paragraph("Scan Results", h2),
            Paragraph(f"Total junk found: <b>{_human_size(total)}</b>  ·  "
                      f"Categories: {len(categories)}", normal),
            Spacer(1, 0.3*cm),
        ]
        risk_colours = {
            "low":    colors.HexColor("#3ddc84"),
            "medium": colors.HexColor("#f0a500"),
            "expert": colors.HexColor("#e05252"),
        }
        table_data = [["Category", "Risk", "Size", "Files", "Recommended"]]
        for cat in sorted(categories, key=lambda c: c.size_bytes, reverse=True):
            table_data.append([
                cat.name, cat.risk.upper(), cat.size_human,
                str(cat.file_count), "Yes" if cat.recommended else "No",
            ])
        tbl = Table(table_data, colWidths=[7*cm, 2*cm, 2.5*cm, 2*cm, 3*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#242938")),
            ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
            ("FONTSIZE",    (0, 0), (-1, 0),  9),
            ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor("#f4f6fb"), colors.white]),
            ("FONTSIZE",    (0, 1), (-1, -1), 9),
            ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d5e8")),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",  (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0),(-1, -1), 4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4*cm))

    if results:
        total_freed   = sum(r.freed_bytes for r in results)
        total_deleted = sum(r.deleted_count for r in results)
        mode = "Dry Run" if (results and results[0].dry_run) else "Actual Clean"
        story += [
            Paragraph(f"Clean Results — {mode}", h2),
            Paragraph(
                f"Total freed: <b>{_human_size(total_freed)}</b>  ·  "
                f"Items removed: <b>{total_deleted}</b>", normal),
            Spacer(1, 0.3*cm),
        ]
        table_data = [["Category", "Freed", "Items", "Skipped", "Errors"]]
        for r in results:
            table_data.append([
                r.category_name, r.freed_human,
                str(r.deleted_count), str(r.skipped_count),
                str(len(r.errors)),
            ])
        tbl = Table(table_data, colWidths=[7*cm, 2.5*cm, 2*cm, 2*cm, 3*cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#242938")),
            ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor("#f4f6fb"), colors.white]),
            ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d5e8")),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",  (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0),(-1, -1), 4),
        ]))
        story.append(tbl)

    doc.build(story)
    return path
