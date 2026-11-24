from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import pymupdf

from models import InvoiceLine, ParsedInvoice


DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5,
    "juni": 6, "juli": 7, "augustus": 8, "september": 9, "oktober": 10,
    "november": 11, "december": 12,
}


class InvoiceParseError(ValueError):
    pass


def parse_decimal(value: str) -> Decimal:
    text = value.strip().replace("\xa0", "").replace(" ", "")
    text = text.replace(".", "").replace(",", ".")
    if text in {"", "-", "—"}:
        raise InvalidOperation(f"Missing decimal value: {value!r}")
    return Decimal(text)


def extract_pdf_text(path: str | Path) -> str:
    text_parts: list[str] = []
    with pymupdf.open(str(path)) as document:
        for page in document:
            text_parts.append(page.get_text())
    return "\n".join(text_parts)


def parse_invoice_date(text: str) -> date:
    match = re.search(
        r"(?:Amsterdam,\s*)?(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(r"Factuurdatum\s*\n?\s*(\d{2}-\d{2}-\d{4})", text, re.IGNORECASE)
        if match:
            day, month, year = map(int, match.group(1).split("-"))
            return date(year, month, day)
        raise InvoiceParseError("Invoice date not found")

    day = int(match.group(1))
    month = DUTCH_MONTHS.get(match.group(2).lower())
    if month is None:
        raise InvoiceParseError("Invoice date contains an unknown Dutch month")
    return date(int(match.group(3)), month, day)


def parse_labeled_date(text: str, label: str) -> Optional[date]:
    match = re.search(rf"{re.escape(label)}\s*[:\n]\s*(\d{2}-\d{2}-\d{4})", text, re.IGNORECASE)
    if not match:
        label_match = re.search(re.escape(label), text, re.IGNORECASE)
        if not label_match:
            return None
        nearby_match = re.search(r"\d{2}-\d{2}-\d{4}", text[label_match.end():label_match.end() + 60])
        if not nearby_match:
            return None
        match_value = nearby_match.group(0)
    else:
        match_value = match.group(1)
    day, month, year = map(int, match_value.split("-"))
    return date(year, month, day)


def closest_period_date(year: int, month: int, day: int, invoice_date: date) -> date:
    candidates = []
    for candidate_year in (year - 1, year, year + 1):
        try:
            candidates.append(date(candidate_year, month, day))
        except ValueError:
            pass
    return min(candidates, key=lambda candidate: abs((candidate - invoice_date).days))


def parse_period(text: str, invoice_date: date) -> tuple[date, date]:
    match = re.search(
        r"(\d{2})-(\d{2})\s+(?:t/m|tot)\s+(?:(?:t/m\s*)?)(\d{2})-\s*(\d{2})",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise InvoiceParseError(f"Period not found in: {text!r}")
    start_month = int(match.group(2))
    end_month = int(match.group(4))
    start_day = int(match.group(1))
    end_day = int(match.group(3))
    start = closest_period_date(invoice_date.year, start_month, start_day, invoice_date)
    end = closest_period_date(start.year, end_month, end_day, invoice_date)
    if end < start:
        end = date(end.year + 1, end.month, end.day)
    return start, end


def line_type_for(description: str) -> str:
    lowered = description.lower()
    if lowered.startswith("verwachte"):
        return "expected"
    if lowered.startswith("werkelijke"):
        return "actual"
    if lowered.startswith("betaald voor"):
        return "previously_paid"
    return "fixed"


def normalize_unit(unit: str) -> str:
    normalized = unit.strip().lower()
    if normalized in {"m³", "m3", "m�"}:
        return "m3"
    if normalized == "kwh":
        return "kWh"
    return unit


def parse_number_list(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for value in re.findall(r"-?\d{1,3}(?:[.,]\d+)+|-?\d+", text):
        try:
            values.append(parse_decimal(value))
        except (InvalidOperation, ValueError):
            continue
    return values


def extract_euro_values(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    pattern = re.compile(
        r"[�€]\s*\n?\s*(-?\d+(?:[.,]\d+)*)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        values.append(parse_decimal(match.group(1)))
    if not values:
        for match in re.finditer(r"(?m)^(-?\d+(?:[.,]\d+)*)$", text.strip()):
            values.append(parse_decimal(match.group(1)))
    if not values:
        for match in re.finditer(r"(?m)^.*?(-?\d+(?:[.,]\d+)*)\s*$", text):
            values.append(parse_decimal(match.group(1)))
    return values


def parse_fixed_lines(text: str, invoice_date: date) -> tuple[list[InvoiceLine], list[str]]:
    lines: list[InvoiceLine] = []
    warnings: list[str] = []
    specifications = (
        ("Vaste leveringskosten", "fixed_delivery"),
        ("Energiebelasting & ODE", "energy_tax"),
        ("Energiebelasting", "energy_tax"),
        ("Vermindering energiebelasting", "energy_tax_reduction"),
        ("Netbeheerkosten", "grid_costs"),
    )
    pattern = re.compile(
        r"(?m)^(?P<label>Vaste leveringskosten|Energiebelasting(?: & ODE)?|"
        r"Vermindering energiebelasting|Netbeheerkosten)\s*$"
    )
    for match in pattern.finditer(text):
        start = match.start()
        raw_lines = text[start:start + 300].split("\n")
        period_text = next((row for row in raw_lines[1:5] if re.search(r"\d{2}-\d{2}\s+t/m\s+\d{2}-\d{2}", row)), "")
        if not period_text:
            warnings.append(f"Geen periode gevonden voor {match.group('label')}")
            continue
        block = "\n".join(raw_lines[:8])
        amounts = extract_euro_values(block)
        if len(amounts) < 2:
            warnings.append(f"Bedragen ontbreken voor {match.group('label')}")
            continue
        try:
            period_start, period_end = parse_period(period_text, invoice_date)
        except InvoiceParseError as exc:
            warnings.append(str(exc))
            continue
        label = match.group("label")
        category = next(
            category for specification_label, category in specifications
            if label == specification_label
            or (specification_label == "Energiebelasting" and label.startswith("Energiebelasting"))
        )
        lines.append(InvoiceLine(
            category=category,
            line_type="fixed",
            description=match.group("label"),
            period_start=period_start,
            period_end=period_end,
            quantity=None,
            unit=None,
            unit_price_ex_vat=None,
            amount_ex_vat=amounts[0],
            amount_inc_vat=amounts[1],
            raw_text="\n".join(raw_lines[:8]),
        ))
    return lines, warnings


def parse_variable_lines(text: str, invoice_date: date) -> tuple[list[InvoiceLine], list[str]]:
    lines: list[InvoiceLine] = []
    warnings: list[str] = []
    descriptions = (
        "Verwachte kosten", "Werkelijke kosten", "Betaald voor",
        "Verwachte opbrengst", "Werkelijke opbrengst",
    )
    pattern = re.compile(
        r"(?P<description>(?:(?:Verwachte|Werkelijke)\s+(?:kosten|opbrengst)\s+[A-Za-z]+|Betaald voor\s+[A-Za-z]+))",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        start = match.start()
        raw_lines = text[start:start + 350].split("\n")
        period_row = next((row for row in raw_lines[1:4] if re.search(r"\d{2}-\d{2}\s+(?:t/m|tot)", row)), "")
        if not period_row:
            warnings.append(f"Geen periode gevonden voor {match.group('description')}")
            continue
        quantity_unit: Optional[Decimal] = None
        unit: Optional[str] = None
        unit_price: Optional[Decimal] = None
        amount_ex: Optional[Decimal] = None
        amount_inc: Optional[Decimal] = None
        for row in raw_lines[1:8]:
            quantity_match = re.search(r"(-?\d+(?:[.,]\d+)*)\s*(m[³3�]|kWh)", row, re.IGNORECASE)
            if quantity_match:
                quantity_unit = parse_decimal(quantity_match.group(1))
                unit = normalize_unit(quantity_match.group(2))
        values = extract_euro_values("\n".join(raw_lines[1:12]))
        if values:
            unit_price = values[0] if len(values) >= 3 else None
            amount_ex = values[1] if len(values) >= 3 else None
            amount_inc = values[2] if len(values) >= 3 else None
        if quantity_unit is None or unit is None or unit_price is None or amount_ex is None or amount_inc is None:
            warnings.append(f"Incomplete variabele regel: {match.group('description')}")
            continue
        try:
            period_start, period_end = parse_period(period_row, invoice_date)
        except InvoiceParseError as exc:
            warnings.append(str(exc))
            continue
        lower_description = match.group("description").lower()
        if "opbrengst" in lower_description:
            category = "feed_in"
        elif unit == "m3":
            category = "gas"
        else:
            category = "electricity"
        lines.append(InvoiceLine(
            category=category,
            line_type=line_type_for(match.group("description")),
            description=match.group("description"),
            period_start=period_start,
            period_end=period_end,
            quantity=quantity_unit,
            unit=unit,
            unit_price_ex_vat=unit_price,
            amount_ex_vat=amount_ex,
            amount_inc_vat=amount_inc,
            raw_text="\n".join(raw_lines[:8]),
        ))
    return lines, warnings


def parse_rebate_lines(text: str, invoice_date: date) -> tuple[list[InvoiceLine], list[str]]:
    lines: list[InvoiceLine] = []
    warnings: list[str] = []
    pattern = re.compile(
        r"(?P<description>Bonusvergoeding[^\\n]*|Terugleverbonus[^\\n]*)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        raw_lines = text[match.start():match.start() + 220].split("\n")
        period_row = ""
        for index, row in enumerate(raw_lines[1:6], start=1):
            if re.search(r"\d{2}-\d{2}\s+(?:t/m|tot)", row):
                period_row = row
                if index + 1 < len(raw_lines) and re.match(r"^\s*\d{2}\s*$", raw_lines[index + 1]):
                    period_row = f"{row}{raw_lines[index + 1].strip()}"
                break
        if not period_row:
            warnings.append(f"Geen periode gevonden voor {match.group('description')}")
            continue
        quantity_match = re.search(
            r"(-?\d+(?:[.,]\d+)*)\s*(kWh)",
            "\n".join(raw_lines[1:7]),
            re.IGNORECASE,
        )
        if not quantity_match:
            warnings.append(f"Verbruik ontbreekt voor {match.group('description')}")
            continue
        values = extract_euro_values("\n".join(raw_lines[1:7]))
        if len(values) < 2:
            warnings.append(f"Bedragen ontbreken voor {match.group('description')}")
            continue
        try:
            period_start, period_end = parse_period(period_row, invoice_date)
        except InvoiceParseError as exc:
            warnings.append(str(exc))
            continue
        description = match.group("description").strip()
        category = "feed_in" if description.lower().startswith("teruglever") else "electricity"
        lines.append(InvoiceLine(
            category=category,
            line_type="fixed",
            description=description,
            period_start=period_start,
            period_end=period_end,
            quantity=parse_decimal(quantity_match.group(1)),
            unit="kWh",
            unit_price_ex_vat=None,
            amount_ex_vat=values[0],
            amount_inc_vat=values[1],
            raw_text="\n".join(raw_lines[:7]),
        ))
    return lines, warnings


def parse_frank_invoice(path: str | Path) -> ParsedInvoice:
    source = Path(path)
    text = extract_pdf_text(source)
    if "Frank Energie" not in text and "frankenergie" not in text.lower():
        raise InvoiceParseError("PDF is not a Frank Energie invoice")

    number_match = re.search(r"Factuurnummer\s*:?\s*\n?\s*(\d{6,})", text, re.IGNORECASE)
    if not number_match:
        number_match = re.search(r"Factuurnummer\s+(\d{6,})", text, re.IGNORECASE)
    if not number_match:
        raise InvoiceParseError("Invoice number not found")

    invoice_date = parse_invoice_date(text)
    due_date = parse_labeled_date(text, "Vervaldatum")
    title_match = re.search(
        r"(?m)^\s*((?:Termijnfactuur|Kortingsfactuur)\s+[^\r\n]+)",
        text,
        re.IGNORECASE,
    )
    invoice_title = title_match.group(1).strip() if title_match else None
    vat_matches = list(re.finditer(r"BTW(?: 21%)?", text, re.IGNORECASE))
    vat_amount = None
    for vat_label in reversed(vat_matches):
        nearby_numbers = re.findall(
            r"-?\d+(?:[.,]\d+)",
            text[vat_label.end():vat_label.end() + 60],
        )
        if nearby_numbers:
            vat_amount = parse_decimal(nearby_numbers[0])
            break

    total_values: list[Decimal] = []
    total_label_text = ""
    total_labels = [match.start() for match in re.finditer(r"(?:Totaal|Notabedrag)", text, re.IGNORECASE)]
    if total_labels:
        for label_index in reversed(total_labels):
            total_block = text[label_index:label_index + 80]
            total_label_text = text[label_index:label_index + 12]
            total_values = extract_euro_values(total_block)
            if total_values:
                break
            nearby_numbers = re.findall(r"-?\d+(?:[.,]\d+)", text[label_index:label_index + 80])
            if nearby_numbers:
                total_values = [parse_decimal(nearby_numbers[-1])]
    if not total_values:
        raise InvoiceParseError("Invoice total not found")
    if "notabedrag" in total_label_text.lower():
        total = max(total_values, key=lambda value: abs(value))
    else:
        total = total_values[-1]

    lines: list[InvoiceLine] = []
    warnings: list[str] = []
    fixed_lines, fixed_warnings = parse_fixed_lines(text, invoice_date)
    variable_lines, variable_warnings = parse_variable_lines(text, invoice_date)
    rebate_lines, rebate_warnings = parse_rebate_lines(text, invoice_date)
    lines.extend(fixed_lines)
    lines.extend(variable_lines)
    lines.extend(rebate_lines)
    warnings.extend(fixed_warnings)
    warnings.extend(variable_warnings)
    warnings.extend(rebate_warnings)

    if not lines:
        warnings.append("Geen factuurregels herkend")

    return ParsedInvoice(
        supplier="Frank Energie",
        invoice_number=number_match.group(1),
        invoice_date=invoice_date,
        due_date=due_date,
        total_amount_inc_vat=total,
        vat_amount=vat_amount,
        lines=lines,
        warnings=warnings,
        invoice_title=invoice_title,
    )
