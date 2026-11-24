from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from models import ParsedInvoice
from parser import extract_pdf_text, parse_frank_invoice


SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_title TEXT,
    invoice_date TEXT NOT NULL,
    due_date TEXT,
    total_amount_inc_vat TEXT NOT NULL,
    vat_amount TEXT,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL,
    import_status TEXT NOT NULL DEFAULT 'imported',
    import_warning TEXT
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    line_type TEXT NOT NULL,
    description TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    quantity TEXT,
    unit TEXT,
    unit_price_ex_vat TEXT,
    amount_ex_vat TEXT,
    amount_inc_vat TEXT,
    raw_text TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(database: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(invoices)")
    }
    if "invoice_title" not in columns:
        connection.execute("ALTER TABLE invoices ADD COLUMN invoice_title TEXT")
    return connection


def is_hash_imported(connection: sqlite3.Connection, source_hash: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM invoices WHERE source_hash = ?", (source_hash,)
    ).fetchone()
    return row is not None


def import_invoice(
    connection: sqlite3.Connection,
    path: Path,
    parsed: Optional[ParsedInvoice] = None,
) -> tuple[str, str]:
    source_hash = sha256_file(path)
    if is_hash_imported(connection, source_hash):
        return "duplicate", "Already imported"

    invoice = parsed or parse_frank_invoice(path)
    warning_text = "\n".join(invoice.warnings) if invoice.warnings else None
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with connection:
        cursor = connection.execute(
            """
            INSERT INTO invoices (
                supplier, invoice_number, invoice_title, invoice_date, due_date,
                total_amount_inc_vat, vat_amount, source_file, source_hash,
                imported_at, import_status, import_warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice.supplier,
                invoice.invoice_number,
                invoice.invoice_title,
                invoice.invoice_date.isoformat(),
                invoice.due_date.isoformat() if invoice.due_date else None,
                str(invoice.total_amount_inc_vat),
                str(invoice.vat_amount) if invoice.vat_amount is not None else None,
                path.name,
                source_hash,
                imported_at,
                "warning" if invoice.warnings else "imported",
                warning_text,
            ),
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
                (
                    invoice_id,
                    line.category,
                    line.line_type,
                    line.description,
                    line.period_start.isoformat(),
                    line.period_end.isoformat(),
                    str(line.quantity) if line.quantity is not None else None,
                    line.unit,
                    str(line.unit_price_ex_vat) if line.unit_price_ex_vat is not None else None,
                    str(line.amount_ex_vat) if line.amount_ex_vat is not None else None,
                    str(line.amount_inc_vat) if line.amount_inc_vat is not None else None,
                    line.raw_text,
                )
                for line in invoice.lines
            ],
        )
    return "imported", warning_text or ""


def backfill_invoice_titles(
    connection: sqlite3.Connection,
    folders: Iterable[Path],
) -> list[str]:
    pdfs_by_name = {
        path.name: path
        for folder in folders
        if folder.exists()
        for path in folder.glob("*.pdf")
    }
    rows = connection.execute(
        "SELECT id, source_file FROM invoices WHERE invoice_title IS NULL OR invoice_title = ''"
    ).fetchall()
    warnings = []
    for row in rows:
        path = pdfs_by_name.get(row["source_file"])
        if path is None:
            warnings.append(f"{row['source_file']}: bronbestand niet gevonden; titel ontbreekt")
            continue
        try:
            invoice = parse_frank_invoice(path)
        except (OSError, ValueError) as exc:
            warnings.append(f"{row['source_file']}: titel kon niet worden uitgelezen ({exc})")
            continue
        if invoice.invoice_title:
            with connection:
                connection.execute(
                    "UPDATE invoices SET invoice_title = ? WHERE id = ?",
                    (invoice.invoice_title, row["id"]),
                )
    return warnings


def scan_and_import(connection: sqlite3.Connection, folders: Iterable[Path]) -> list[dict]:
    results = []
    for folder in folders:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.pdf")):
            source_hash = sha256_file(path)
            if is_hash_imported(connection, source_hash):
                results.append({"file": path.name, "status": "duplicate", "warning": ""})
                continue
            try:
                status, warning = import_invoice(connection, path)
            except Exception as exc:
                results.append({"file": path.name, "status": "error", "warning": str(exc)})
            else:
                results.append({"file": path.name, "status": status, "warning": warning})
    return results
