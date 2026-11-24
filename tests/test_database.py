import pymupdf

from database import backfill_invoice_titles, connect, import_invoice, is_hash_imported
from models import ParsedInvoice
from datetime import date
from decimal import Decimal
from pathlib import Path


def test_duplicate_import_is_skipped(tmp_path):
    pdf_path = tmp_path / "invoice.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Synthetische factuur")
    document.save(pdf_path)
    document.close()

    parsed = ParsedInvoice(
        supplier="Frank Energie",
        invoice_number="99999999",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 1, 10),
        total_amount_inc_vat=Decimal("10.00"),
        vat_amount=Decimal("1.74"),
        lines=[],
        warnings=["synthetic"],
    )
    connection = connect(tmp_path / "test.db")
    status, _ = import_invoice(connection, pdf_path, parsed)
    assert status == "imported"
    status, _ = import_invoice(connection, pdf_path, parsed)
    assert status == "duplicate"


def test_backfill_titles_for_existing_invoices(tmp_path):
    pdf_path = tmp_path / "pdfs" / "26085608.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes((Path("pdfs") / "26085608.pdf").read_bytes())
    connection = connect(tmp_path / "test.db")
    connection.execute(
        """
        INSERT INTO invoices (
            supplier, invoice_number, invoice_date, total_amount_inc_vat,
            source_file, source_hash, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("Frank Energie", "26085608", "2026-03-30", "-0.53",
         "26085608.pdf", "legacy-hash", "2026-03-30"),
    )
    connection.commit()

    warnings = backfill_invoice_titles(connection, [pdf_path.parent])

    assert warnings == []
    title = connection.execute(
        "SELECT invoice_title FROM invoices WHERE invoice_number = '26085608'"
    ).fetchone()["invoice_title"]
    assert title == "Kortingsfactuur Februari 2026"
