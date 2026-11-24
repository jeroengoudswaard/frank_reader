from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any


def decimal_sum(rows: list[sqlite3.Row], column: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        value = row[column]
        if value is not None:
            total += Decimal(value)
    return total


def ytd_overview(connection: sqlite3.Connection, year: int) -> dict[str, Any]:
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    actual = connection.execute(
        """
        SELECT category, line_type, quantity, unit, unit_price_ex_vat,
               amount_ex_vat, amount_inc_vat, period_start, period_end
        FROM invoice_lines
        WHERE line_type = 'actual'
          AND period_start >= ? AND period_start <= ?
        """,
        (start, end),
    ).fetchall()

    electricity = [row for row in actual if row["category"] == "electricity"]
    feed_in = [row for row in actual if row["category"] == "feed_in"]
    gas = [row for row in actual if row["category"] == "gas"]

    ytd_cost = Decimal("0")
    invoice_rows = connection.execute(
        "SELECT total_amount_inc_vat FROM invoices WHERE invoice_date >= ? AND invoice_date <= ?",
        (start, end),
    ).fetchall()
    ytd_cost = decimal_sum(invoice_rows, "total_amount_inc_vat")

    def quantity_total(rows: list[sqlite3.Row]) -> Decimal:
        return sum((Decimal(row["quantity"]) for row in rows if row["quantity"] is not None), Decimal("0"))

    return {
        "ytd_cost_inc_vat": ytd_cost,
        "electricity_kwh": quantity_total(electricity),
        "feed_in_kwh": abs(quantity_total(feed_in)),
        "gas_m3": quantity_total(gas),
        "actual_lines": actual,
    }


def monthly_consumption(connection: sqlite3.Connection, year: int) -> list[dict]:
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    rows = connection.execute(
        """
        SELECT category, line_type, quantity, unit, unit_price_ex_vat,
               amount_ex_vat, amount_inc_vat, period_start, period_end
        FROM invoice_lines
        WHERE (
            line_type = 'actual'
            OR (
                line_type = 'fixed'
                AND category IN (
                    'fixed_delivery', 'energy_tax', 'energy_tax_reduction', 'grid_costs'
                )
            )
        )
          AND period_start >= ? AND period_start <= ?
        ORDER BY period_start
        """,
        (start, end),
    ).fetchall()

    grouped: dict[str, dict] = {}
    for row in rows:
        month = row["period_start"][:7]
        item = grouped.setdefault(month, {
            "month": month,
            "electricity_kwh": Decimal("0"),
            "feed_in_kwh": Decimal("0"),
            "gas_m3": Decimal("0"),
            "electricity_cost_inc_vat": Decimal("0"),
            "feed_in_revenue_inc_vat": Decimal("0"),
            "gas_cost_inc_vat": Decimal("0"),
            "fixed_delivery_cost_inc_vat": Decimal("0"),
            "energy_tax_cost_inc_vat": Decimal("0"),
            "energy_tax_reduction_inc_vat": Decimal("0"),
            "grid_cost_inc_vat": Decimal("0"),
            "fixed_cost_inc_vat": Decimal("0"),
        })
        quantity = Decimal(row["quantity"]) if row["quantity"] is not None else Decimal("0")
        amount = Decimal(row["amount_inc_vat"]) if row["amount_inc_vat"] is not None else Decimal("0")
        if row["line_type"] == "fixed":
            fixed_column = {
                "fixed_delivery": "fixed_delivery_cost_inc_vat",
                "energy_tax": "energy_tax_cost_inc_vat",
                "energy_tax_reduction": "energy_tax_reduction_inc_vat",
                "grid_costs": "grid_cost_inc_vat",
            }[row["category"]]
            item[fixed_column] += amount
            item["fixed_cost_inc_vat"] += amount
        elif row["category"] == "electricity":
            item["electricity_kwh"] += quantity
            item["electricity_cost_inc_vat"] += amount
        elif row["category"] == "feed_in":
            item["feed_in_kwh"] += abs(quantity)
            item["feed_in_revenue_inc_vat"] += amount
        elif row["category"] == "gas":
            item["gas_m3"] += quantity
            item["gas_cost_inc_vat"] += amount
    return list(grouped.values())


def average_prices_by_month(connection: sqlite3.Connection, year: int) -> list[dict]:
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    rows = connection.execute(
        """
        SELECT category, quantity, amount_inc_vat, period_start
        FROM invoice_lines
        WHERE line_type = 'actual'
          AND category IN ('gas', 'electricity', 'feed_in')
          AND period_start >= ? AND period_start <= ?
        ORDER BY period_start
        """,
        (start, end),
    ).fetchall()

    price_keys = {
        "gas": "gas_price_inc_vat",
        "electricity": "electricity_price_inc_vat",
        "feed_in": "feed_in_price_inc_vat",
    }

    def empty_totals() -> dict[str, list[Decimal]]:
        return {key: [Decimal("0"), Decimal("0")] for key in price_keys.values()}

    monthly_totals: dict[str, dict[str, list[Decimal]]] = {}
    yearly_totals = empty_totals()
    for row in rows:
        if row["quantity"] is None or row["amount_inc_vat"] is None:
            continue
        quantity = abs(Decimal(row["quantity"]))
        if quantity == 0:
            continue

        amount = Decimal(row["amount_inc_vat"])
        if row["category"] == "feed_in":
            amount = abs(amount)

        key = price_keys[row["category"]]
        month = row["period_start"][:7]
        month_totals = monthly_totals.setdefault(month, empty_totals())
        month_totals[key][0] += amount
        month_totals[key][1] += quantity
        yearly_totals[key][0] += amount
        yearly_totals[key][1] += quantity

    def prices_for(period: str, totals: dict[str, list[Decimal]]) -> dict:
        return {
            "period": period,
            **{
                key: amount / quantity if quantity else None
                for key, (amount, quantity) in totals.items()
            },
        }

    result = [
        prices_for(month, monthly_totals[month])
        for month in sorted(monthly_totals)
    ]
    if any(quantity for _, quantity in yearly_totals.values()):
        result.append(prices_for("Jaargemiddelde", yearly_totals))
    return result
