from datetime import date
from decimal import Decimal

from calculations import average_prices_by_month, monthly_consumption, ytd_overview
from database import connect


def test_aggregation_by_consumption_period(tmp_path):
    connection = connect(tmp_path / "test.db")
    cursor = connection.execute(
        """
        INSERT INTO invoices (
            supplier, invoice_number, invoice_date, due_date,
            total_amount_inc_vat, vat_amount, source_file, source_hash,
            imported_at, import_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Frank Energie", "1", "2026-07-11", "2026-07-18",
         "162.45", "28.19", "synthetic.pdf", "hash", "2026-07-11", "imported"),
    )
    invoice_id = cursor.lastrowid
    connection.executemany(
        """
        INSERT INTO invoice_lines (
            invoice_id, category, line_type, description, period_start,
            period_end, quantity, unit, unit_price_ex_vat,
            amount_ex_vat, amount_inc_vat, raw_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (invoice_id, "electricity", "actual", "Werkelijke kosten", "2026-06-01",
             "2026-06-30", "280", "kWh", "0.140", "39.08", "47.28", "synthetic"),
            (invoice_id, "feed_in", "actual", "Werkelijke opbrengst", "2026-06-01",
             "2026-06-30", "-208", "kWh", "0.079", "-16.50", "-19.96", "synthetic"),
            (invoice_id, "gas", "actual", "Werkelijke kosten", "2026-06-01",
             "2026-06-30", "20", "m3", "0.504", "9.96", "12.06", "synthetic"),
            (invoice_id, "fixed_delivery", "fixed", "Vaste leveringskosten", "2026-06-01",
             "2026-06-30", None, None, None, "10.00", "12.10", "synthetic"),
            (invoice_id, "energy_tax_reduction", "fixed", "Vermindering energiebelasting", "2026-06-01",
             "2026-06-30", None, None, None, "-5.00", "-6.05", "synthetic"),
        ],
    )
    connection.commit()

    monthly = monthly_consumption(connection, 2026)
    assert monthly[0]["month"] == "2026-06"
    assert monthly[0]["electricity_kwh"] == Decimal("280")
    assert monthly[0]["feed_in_kwh"] == Decimal("208")
    assert monthly[0]["gas_m3"] == Decimal("20")
    assert monthly[0]["fixed_delivery_cost_inc_vat"] == Decimal("12.10")
    assert monthly[0]["energy_tax_reduction_inc_vat"] == Decimal("-6.05")
    assert monthly[0]["fixed_cost_inc_vat"] == Decimal("6.05")

    overview = ytd_overview(connection, 2026)
    assert overview["electricity_kwh"] == Decimal("280")
    assert overview["feed_in_kwh"] == Decimal("208")


def test_monthly_prices_and_usage_weighted_yearly_averages(tmp_path):
    connection = connect(tmp_path / "test.db")
    cursor = connection.execute(
        """
        INSERT INTO invoices (
            supplier, invoice_number, invoice_date, total_amount_inc_vat,
            source_file, source_hash, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("Frank Energie", "2", "2026-03-01", "0", "synthetic.pdf",
         "weighted-prices", "2026-03-01"),
    )
    invoice_id = cursor.lastrowid
    connection.executemany(
        """
        INSERT INTO invoice_lines (
            invoice_id, category, line_type, description, period_start,
            period_end, quantity, unit, amount_inc_vat
        ) VALUES (?, ?, 'actual', 'Werkelijke kosten', ?, ?, ?, ?, ?)
        """,
        [
            (invoice_id, "electricity", "2026-02-01", "2026-02-28", "100", "kWh", "30.00"),
            (invoice_id, "gas", "2026-02-01", "2026-02-28", "80", "m3", "64.00"),
            (invoice_id, "feed_in", "2026-02-01", "2026-02-28", "-10", "kWh", "-2.00"),
            (invoice_id, "electricity", "2026-06-01", "2026-06-30", "280", "kWh", "47.28"),
            (invoice_id, "gas", "2026-06-01", "2026-06-30", "20", "m3", "12.06"),
            (invoice_id, "feed_in", "2026-06-01", "2026-06-30", "-208", "kWh", "-19.96"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO invoice_lines (
            invoice_id, category, line_type, description, period_start,
            period_end, quantity, unit, amount_inc_vat
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (invoice_id, "gas", "expected", "Verwachte kosten", "2026-02-01",
             "2026-02-28", "1000", "m3", "1000.00"),
            (invoice_id, "electricity", "previously_paid", "Betaald voor",
             "2026-06-01", "2026-06-30", "-500", "kWh", "-50.00"),
        ],
    )
    connection.commit()

    prices = average_prices_by_month(connection, 2026)
    february, june, annual = prices
    assert february["period"] == "2026-02"
    assert february["electricity_price_inc_vat"] == Decimal("0.30")
    assert february["gas_price_inc_vat"] == Decimal("0.80")
    assert february["feed_in_price_inc_vat"] == Decimal("0.20")
    assert june["electricity_price_inc_vat"] == Decimal("47.28") / Decimal("280")
    assert june["feed_in_price_inc_vat"] == Decimal("19.96") / Decimal("208")
    assert annual["period"] == "Jaargemiddelde"
    assert annual["electricity_price_inc_vat"] == Decimal("77.28") / Decimal("380")
    assert annual["gas_price_inc_vat"] == Decimal("76.06") / Decimal("100")
    assert annual["feed_in_price_inc_vat"] == Decimal("21.96") / Decimal("218")
