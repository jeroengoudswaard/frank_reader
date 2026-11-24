from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class InvoiceLine:
    category: str
    line_type: str
    description: str
    period_start: date
    period_end: date
    quantity: Optional[Decimal]
    unit: Optional[str]
    unit_price_ex_vat: Optional[Decimal]
    amount_ex_vat: Optional[Decimal]
    amount_inc_vat: Optional[Decimal]
    raw_text: str


@dataclass
class ParsedInvoice:
    supplier: str
    invoice_number: str
    invoice_date: date
    due_date: Optional[date]
    total_amount_inc_vat: Decimal
    vat_amount: Optional[Decimal]
    lines: list[InvoiceLine]
    warnings: list[str]
    invoice_title: Optional[str] = None
