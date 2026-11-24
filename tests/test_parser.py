from datetime import date
from decimal import Decimal

from models import InvoiceLine, ParsedInvoice
from parser import parse_decimal, parse_frank_invoice


SYNTHETIC_TEXT = """
Factuurnummer:
12345678
Amsterdam, 11 juli 2026
Vervaldatum:
18-07-2026
Frank Energie
Notabedrag
Incl. 28,19 BTW
€ 162,45
Vaste leveringskosten
01-08 t/m 31-08
€ 11,98
€ 14,50
Verwachte kosten aug
01-08 t/m 31-08
18 m³
€ 0,513
€ 9,23
€ 11,17
Werkelijke kosten jun
01-06 t/m 30-06
20 m³
€ 0,504
€ 9,96
€ 12,06
Betaald voor jun
01-06 t/m 30-06
-21 m³
€ 0,553
€ -11,63
€ -14,07
Werkelijke kosten jun
01-06 t/m 30-06
280 kWh
€ 0,140
€ 39,08
€ 47,28
Werkelijke opbrengst jun
01-06 t/m 30-06
-208 kWh
€ 0,079
€ -16,50
€ -19,96
BTW 21%
€ 28,19
"""


def test_decimal_comma_and_negative():
    assert parse_decimal("162,45") == Decimal("162.45")
    assert parse_decimal("-245") == Decimal("-245")


def test_parser_metadata_and_line_types(tmp_path):
    # Synthetic data; not a real household invoice.
    import pymupdf

    path = tmp_path / "synthetic.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), SYNTHETIC_TEXT)
    document.save(path)
    document.close()

    invoice = parse_frank_invoice(path)
    assert invoice.invoice_number == "12345678"
    assert invoice.invoice_date == date(2026, 7, 11)
    assert invoice.due_date == date(2026, 7, 18)
    assert invoice.total_amount_inc_vat == Decimal("162.45")
    assert invoice.vat_amount == Decimal("28.19")

    actual = [line for line in invoice.lines if line.line_type == "actual"]
    classifications = {line.category: line.line_type for line in actual}
    assert classifications["gas"] == "actual"
    assert classifications["electricity"] == "actual"
    assert classifications["feed_in"] == "actual"

    paid = [line for line in invoice.lines if line.line_type == "previously_paid"]
    assert paid and paid[0].quantity == Decimal("-21")


def test_real_invoice_parser():
    invoice = parse_frank_invoice("pdfs/26387205.pdf")
    assert invoice.invoice_number == "26387205"
    assert invoice.invoice_title == "Termijnfactuur augustus"
    assert invoice.invoice_date == date(2026, 7, 11)
    electricity_actual = [
        line for line in invoice.lines
        if line.category == "electricity" and line.line_type == "actual"
    ]
    assert len(electricity_actual) == 1
    assert electricity_actual[0].quantity == Decimal("280")
    assert electricity_actual[0].unit_price_ex_vat == Decimal("0.140")


def test_real_rebate_invoice_parser():
    invoice = parse_frank_invoice("pdfs/26085608.pdf")
    assert invoice.invoice_number == "26085608"
    assert invoice.invoice_title == "Kortingsfactuur Februari 2026"
    assert invoice.invoice_date == date(2026, 3, 30)
    assert invoice.due_date == date(2026, 3, 31)
    assert invoice.total_amount_inc_vat == Decimal("-0.53")
    assert invoice.vat_amount == Decimal("-0.09")

    bonuses = [line for line in invoice.lines if line.category in {"electricity", "feed_in"}]
    assert len(bonuses) == 2
