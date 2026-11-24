# COPILOT.md - Energy Dashboard

## Project goal
Build a small, local application that automatically reads Dutch household energy invoices, starting with Frank Energie PDF invoices, stores normalized data, and creates useful overviews of electricity, gas, feed-in, prices and costs.

The application is for one household. Keep it simple, transparent and local-first. Do not introduce cloud services, Docker, authentication or a separate database server unless explicitly requested.

## Preferred stack
- Python 3.12+
- Streamlit for the UI
- SQLite for persistent storage
- pandas for transformations and summaries
- PyMuPDF (`pymupdf`) for PDF text extraction
- Plotly for interactive charts
- pytest for parser and calculation tests

Prefer the Python standard library where practical.

## Development environment
The project must be easy to develop and run from VS Code.

Suggested commands:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
pip install streamlit pandas pymupdf plotly pytest
streamlit run app.py
```

Maintain a `requirements.txt` (or `pyproject.toml` if the project later adopts one consistently).

## Architecture
Keep responsibilities separated:

```text
energy-dashboard/
├── COPILOT.md
├── app.py                  # Streamlit entry point
├── parser.py               # Frank Energie PDF parser
├── database.py             # SQLite schema and CRUD/import helpers
├── models.py               # Optional typed/dataclass domain models
├── calculations.py         # Aggregations/KPIs, no UI logic
├── invoices/               # Input PDFs; never modify originals
├── tests/
│   ├── test_parser.py
│   └── test_calculations.py
├── energy.db               # Local database; generated
└── requirements.txt
```

Do not place parsing, database access and chart logic all in `app.py`.

## Core workflow
1. Scan `invoices/` for PDF files.
2. Create a SHA-256 hash for each file.
3. Skip a file if that hash has already been imported successfully.
4. Extract PDF text with PyMuPDF.
5. Determine the invoice type/supplier. Initially support Frank Energie only.
6. Parse invoice metadata and individual invoice lines.
7. Validate parsed values before writing anything.
8. Store normalized records in SQLite in a single transaction.
9. Show import warnings/errors in Streamlit without crashing the entire app.
10. Build summaries from the database, not directly from the PDFs.

Imports must be idempotent: scanning the same directory twice must not create duplicate records.

## Important accounting/data rule
Never assume that the invoice month equals the consumption month.

Frank Energie invoices can contain several time concepts in one invoice, for example:
- expected costs for a future/current month;
- actual consumption/costs for an earlier month;
- amounts or consumption previously paid/settled for that earlier month.

Store the period and the meaning of each line explicitly. Do not assign all lines to the invoice date.

## Data model
Use a normalized model. A reasonable starting schema is:

### invoices
- id
- supplier
- invoice_number
- invoice_date
- due_date
- total_amount_inc_vat
- vat_amount
- source_file
- source_hash UNIQUE
- imported_at

### invoice_lines
- id
- invoice_id
- category
- line_type
- description
- period_start
- period_end
- quantity
- unit
- unit_price_ex_vat
- amount_ex_vat
- amount_inc_vat
- raw_text

Useful `category` values:
- electricity
- gas
- feed_in
- fixed_delivery
- energy_tax
- energy_tax_reduction
- grid_costs
- other

Useful `line_type` values:
- expected
- actual
- previously_paid
- fixed

Do not encode these as translated display strings throughout the code. Use stable internal values and map them to Dutch labels in the UI.

## Frank Energie parsing
Initially optimize for the known Frank Energie invoice layout rather than attempting a universal invoice parser.

Expected fields include:
- invoice number
- invoice date
- due date
- invoice total including VAT
- VAT amount
- fixed delivery costs
- energy tax / ODE where present
- energy-tax reduction
- grid operator costs
- gas usage in m3
- electricity consumption in kWh
- electricity feed-in in kWh
- price per m3 or kWh
- amounts excluding and including VAT
- period start/end
- whether a variable line represents expected, actual or previously paid values

Do not rely on fixed PDF coordinates unless text parsing genuinely cannot identify a field. Prefer labels, sections and regex patterns because layouts may shift slightly between invoices.

### Number parsing
Dutch invoice formats require explicit handling:
- `€ 162,45` -> Decimal("162.45")
- `-245 kWh` remains negative when the invoice represents feed-in that way
- decimal comma must be supported
- thousands separators must not be confused with decimal separators
- `m³` and `m3` should normalize to `m3`

For money, avoid binary floating-point calculations where cents matter. Prefer `decimal.Decimal` while parsing/calculating monetary values; convert only where a plotting library requires numeric floats.

## Validation
A parser must fail visibly rather than silently storing questionable data.

Check where possible:
- invoice number exists;
- invoice date exists;
- total amount was parsed;
- period end is not before period start;
- units match categories (`kWh` electricity/feed-in, `m3` gas);
- duplicate source hash is rejected/skipped;
- parsed totals reconcile reasonably with the invoice total when the invoice structure allows this.

Do not silently replace missing values with zero. Use `None`/NULL and surface an import warning.

Keep `raw_text` for parsed invoice lines so anomalies can be traced back to the source without reparsing the complete PDF.

## Dashboard requirements
Use Dutch labels in the user-facing UI even though code/identifiers can be English.

### Overview page
Show at least:
- energiekosten YTD
- elektriciteit afgenomen YTD (kWh)
- elektriciteit teruggeleverd YTD (kWh)
- gasverbruik YTD (m3)

Charts:
- monthly total energy costs
- electricity imported vs feed-in by consumption period
- gas consumption by consumption period
- average electricity purchase price
- average feed-in price

### Electricity page
Monthly table and charts for:
- consumption kWh
- feed-in kWh
- net grid consumption
- average purchase price per kWh
- average feed-in price per kWh
- actual electricity costs/revenues

Keep gross consumption and feed-in available. Do not store only the net value.

### Gas page
Monthly:
- m3 consumed
- average price per m3
- actual cost

### Invoices page
Show:
- invoice number
- invoice date
- due date
- invoice amount
- source filename
- import status/warnings where relevant

## Calculation rules
- Default usage/cost trend charts should use `actual` lines, not forecasts.
- Expected/forecast values may be shown separately but must be clearly labelled.
- Previously paid/settlement lines are financial reconciliation entries and must not be counted as new physical consumption.
- Feed-in must be distinguishable from electricity consumption even if quantities or amounts are negative in the source invoice.
- Net electricity = grid consumption minus absolute feed-in quantity, but also retain both gross values.
- Aggregate by `period_start`/`period_end`, not invoice month.
- Make VAT inclusion explicit in names of cost metrics. Default dashboard monetary totals to amounts including VAT unless otherwise stated.

## Future extensions
Design the code so these can be added without rewriting the core model, but do not implement them unless requested:
- hourly/daily smart-meter or P1 data;
- Frank Energie API/CSV data;
- Zendure SolarFlow battery data;
- solar PV generation;
- dynamic hourly electricity prices;
- comparison of pre/post battery periods;
- heat-pump electricity versus reduced gas consumption;
- additional energy suppliers.

A future high-value view is an energy balance:

`PV generation -> household use + battery charging + grid feed-in`

and

`household electricity demand -> PV direct use + battery discharge + grid import`

Keep the current schema extensible for this, but avoid premature complexity.

## Coding style
- Write straightforward, readable Python.
- Use type hints on public functions.
- Prefer small functions with one purpose.
- Use descriptive names rather than abbreviations.
- Add docstrings where behavior is not obvious.
- Avoid over-engineering and unnecessary classes.
- Do not introduce async code unless a real need arises.
- Do not add a web API/backend layer merely to serve Streamlit.
- Use parameterized SQL, never construct SQL from user/input strings.
- Keep filesystem paths portable with `pathlib.Path`.
- Log useful import information, but never log unnecessary personal/account details from invoices.

## Tests
Parser tests are important because invoice parsing can fail subtly.

For each representative invoice sample, test at least:
- invoice metadata;
- electricity actual quantity and unit price;
- feed-in actual quantity and unit price;
- gas actual quantity and unit price;
- expected versus actual versus previously-paid classification;
- decimal-comma and negative-value parsing;
- duplicate-import handling;
- aggregation by consumption period.

When fixing a parser bug, first add a regression test reproducing the issue.

Do not commit real household invoices to a public repository. Test fixtures should be anonymized extracts or synthetic documents/data that reproduce the relevant structure.

## Privacy and security
Energy invoices contain personal and financial information.

Therefore:
- keep processing local by default;
- never upload invoice contents to external AI/API/cloud services without explicit instruction;
- do not commit `energy.db` or real PDFs;
- ensure `.gitignore` includes at least:

```gitignore
.venv/
__pycache__/
*.pyc
energy.db
invoices/*.pdf
.streamlit/secrets.toml
```

Do not hard-code customer numbers, EAN numbers, addresses, bank details or API secrets.

## How Copilot should work on this repository
When asked to implement a feature:
1. Inspect the existing files before proposing structural changes.
2. Preserve the simple local architecture unless there is a concrete reason to change it.
3. Prefer modifying existing components over creating parallel implementations.
4. Add/update tests for parser and calculation changes.
5. Do not fabricate energy data to make a feature look complete. Clearly mark synthetic test fixtures as synthetic.
6. Explain significant schema migrations before making destructive changes.
7. Never delete or overwrite source invoices.
8. Keep Dutch UI wording concise and normal, not marketing language.
9. If invoice wording/layout is ambiguous, retain the raw line and flag it rather than guessing.
10. At the end of a change, state briefly what changed, which files changed, and how to run/test it.

## First implementation milestone
If this repository is empty, build this MVP first:

1. Basic project/requirements and `.gitignore`.
2. SQLite schema.
3. Frank Energie PDF parser.
4. Folder scanner with duplicate detection using SHA-256.
5. Import one or more PDFs into SQLite.
6. Tests for parser and calculations.
7. Streamlit overview with YTD KPIs and monthly electricity/gas/feed-in charts.
8. Invoice table with import information.

Do not implement authentication, cloud hosting, Docker, P1 integration or battery integration as part of the first milestone.

## Definition of done for MVP
The MVP is done when a user can:
1. copy Frank Energie PDFs into `invoices/`;
2. run `streamlit run app.py`;
3. import new invoices without duplicates;
4. see actual electricity consumption, feed-in and gas by their true consumption periods;
5. see invoice totals separately from consumption-period analytics;
6. restart the app without losing imported data;
7. identify any PDF that could not be parsed reliably.
