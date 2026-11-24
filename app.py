from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from calculations import (
    ENERGY_TAX_EX_VAT,
    annual_energy_totals,
    average_prices_by_month,
    monthly_consumption,
    ytd_overview,
)
from database import backfill_invoice_titles, connect, scan_and_import


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "energy.db"

st.set_page_sidebar_bg_color = getattr(st, "set_page_sidebar_bg_color", lambda color: None)
st.set_page_config(page_title="Frank Energie Dashboard", layout="wide")
st.title("Energieoverzicht")

connection = connect(DATABASE)

folders = [ROOT / "invoices", ROOT / "pdfs"]
for warning in backfill_invoice_titles(connection, folders):
    st.warning(warning)
col1, col2 = st.columns([1, 5])
with col1:
    if st.button("Importeer facturen"):
        results = scan_and_import(connection, folders)
        for result in results:
            if result["status"] == "error":
                st.error(f"{result['file']}: {result['warning']}")
            elif result["status"] == "duplicate":
                st.caption(f"{result['file']}: al geïmporteerd")
            elif result["warning"]:
                st.warning(f"{result['file']}: {result['warning']}")
            else:
                st.success(f"{result['file']}: geïmporteerd")

year = st.number_input("Jaar", min_value=2000, max_value=2100, value=datetime.now().year)
overview = ytd_overview(connection, int(year))

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Factuurbedrag YTD", f"€ {overview['ytd_cost_inc_vat']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
kpi2.metric("Elektriciteit afgenomen YTD", f"{overview['electricity_kwh']} kWh")
kpi3.metric("Elektriciteit teruggeleverd YTD", f"{overview['feed_in_kwh']} kWh")
kpi4.metric("Gasverbruik YTD", f"{overview['gas_m3']} m³")

monthly = monthly_consumption(connection, int(year))
if monthly:
    frame = pd.DataFrame(monthly)
    for column in frame.columns:
        if column != "month" and hasattr(frame[column], "astype"):
            frame[column] = frame[column].astype(float)

    graph_mode = st.radio(
        "Weergave van gas en elektriciteit",
        ("Kosten", "Verbruik"),
        horizontal=True,
    )
    show_costs = graph_mode == "Kosten"
    electricity_columns = (
        ["electricity_cost_inc_vat", "feed_in_revenue_inc_vat"]
        if show_costs
        else ["electricity_kwh", "feed_in_kwh"]
    )
    electricity_labels = (
        ["Afgenomen elektriciteit", "Teruglevering"]
        if show_costs
        else ["Elektriciteit afgenomen", "Elektriciteit teruggeleverd"]
    )
    electricity_frame = frame[["month", *electricity_columns]].rename(
        columns=dict(zip(electricity_columns, electricity_labels))
    )
    electricity_fig = px.bar(
        electricity_frame,
        x="month",
        y=electricity_labels,
        barmode="group",
        labels={
            "month": "Maand",
            "value": "Bedrag incl. btw (€)" if show_costs else "Verbruik (kWh)",
            "variable": "Elektriciteit",
        },
        title="Elektriciteit",
    )

    gas_column = "gas_cost_inc_vat" if show_costs else "gas_m3"
    gas_label = "Gas kosten incl. btw (€)" if show_costs else "Gasverbruik (m³)"
    gas_fig = px.bar(
        frame,
        x="month",
        y=gas_column,
        labels={"month": "Maand", gas_column: gas_label},
        title="Gas",
    )
    gas_fig.update_traces(name=gas_label, showlegend=True)

    fixed_columns = {
        "fixed_delivery_cost_inc_vat": "Vaste leveringskosten",
        "energy_tax_cost_inc_vat": "Energiebelasting",
        "energy_tax_reduction_inc_vat": "Vermindering energiebelasting",
        "grid_cost_inc_vat": "Netbeheerkosten",
    }
    fixed_frame = frame[["month", *fixed_columns]].rename(columns=fixed_columns)
    fixed_labels = list(fixed_columns.values())
    fixed_fig = px.bar(
        fixed_frame,
        x="month",
        y=fixed_labels,
        barmode="stack",
        labels={
            "month": "Maand",
            "value": "Bedrag incl. btw (€)",
            "variable": "Vaste kosten",
        },
        title="Vaste kosten",
    )

    for chart in (electricity_fig, gas_fig, fixed_fig):
        chart.update_layout(
            legend_title_text="",
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
            autosize=True,
        )
        st.plotly_chart(chart, width="stretch")
else:
    st.info("Nog geen werkelijke verbruiksregels of vaste kosten geïmporteerd.")

st.subheader("Gemiddelde energieprijzen")
price_rows = average_prices_by_month(connection, int(year))
if price_rows:
    price_frame = pd.DataFrame(price_rows).rename(columns={
        "period": "Periode",
        "gas_price_inc_vat": "Gas (€/m³ incl. btw)",
        "electricity_price_inc_vat": "Elektriciteit levering (€/kWh incl. btw)",
        "feed_in_price_inc_vat": "Elektriciteit teruglevering (€/kWh incl. btw)",
    })
    for column in price_frame.columns[1:]:
        price_frame[column] = price_frame[column].map(
            lambda value: float(value) if value is not None else None
        )
    st.dataframe(
        price_frame,
        hide_index=True,
        width="stretch",
        alt="Gemiddelde gas- en elektriciteitsprijzen per maand en voor het jaar",
        column_config={
            column: st.column_config.NumberColumn(format="€ %.4f")
            for column in price_frame.columns[1:]
        },
    )
    st.caption(
        "Gebaseerd op werkelijke regels en bedragen inclusief btw. "
        "De energiebelasting is toegevoegd volgens de tarieven van het geselecteerde jaar; "
        "bij teruglevering alleen in 2026. Het jaargemiddelde is gewogen naar de werkelijke "
        "hoeveelheid gas, afgenomen elektriciteit en teruglevering."
    )
    totals = annual_energy_totals(connection, int(year))
    totals_frame = pd.DataFrame([{
        "Periode": "Jaartotalen",
        "Gas (€ incl. btw)": totals["gas_total_inc_vat"],
        "Elektriciteit levering (€ incl. btw)": totals["electricity_total_inc_vat"],
        "Teruglevering (€ incl. btw)": totals["feed_in_total_inc_vat"],
    }])
    for column in totals_frame.columns[1:]:
        totals_frame[column] = totals_frame[column].map(
            lambda value: float(value) if value is not None else None
        )
    st.dataframe(
        totals_frame,
        hide_index=True,
        width="stretch",
        alt="Totale gas- en elektriciteitskosten en terugleveropbrengst voor het jaar",
        column_config={
            column: st.column_config.NumberColumn(format="€ %.2f")
            for column in totals_frame.columns[1:]
        },
    )
    st.caption(
        "Jaartotalen zijn gebaseerd op werkelijk verbruik en teruglevering, inclusief "
        "energiebelasting en btw. Teruglevering wordt als positieve opbrengst getoond."
    )
else:
    st.info("Geen werkelijke energieprijzen gevonden voor dit jaar.")
if int(year) not in ENERGY_TAX_EX_VAT:
    st.warning(
        "Energiebelastingtarieven zijn alleen bekend voor 2026 en 2027. "
        "De getoonde prijzen bevatten voor dit jaar geen energiebelasting."
    )

st.subheader("Facturen")
invoice_rows = connection.execute(
    """
    SELECT id, invoice_number, invoice_date, due_date, total_amount_inc_vat,
           invoice_title, source_file, import_status, import_warning
    FROM invoices
    ORDER BY invoice_date DESC
    """
).fetchall()
if invoice_rows:
    invoice_frame = pd.DataFrame([
        {
            "Nr.": number,
            "Factuurnummer": row["invoice_number"],
            "Factuurdatum": row["invoice_date"],
            "Vervaldatum": row["due_date"] or "—",
            "Bedrag incl. btw": float(row["total_amount_inc_vat"]),
            "Omschrijving": row["invoice_title"] or "—",
            "Bestand": row["source_file"],
            "Status": row["import_status"],
            "Importmelding": row["import_warning"] or "—",
        }
        for number, row in enumerate(invoice_rows, start=1)
    ])
    selection = st.dataframe(
        invoice_frame,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="invoice_table",
        alt="Geïmporteerde facturen",
        column_config={
            "Nr.": st.column_config.NumberColumn(width="small"),
            "Bedrag incl. btw": st.column_config.NumberColumn(format="€ %.2f"),
        },
    )
    selected_rows = selection.selection.rows
    if selected_rows:
        row = invoice_rows[selected_rows[0]]
        source_path = next(
            (folder / row["source_file"] for folder in folders if (folder / row["source_file"]).is_file()),
            None,
        )
        if source_path:
            st.download_button(
                f"Download {row['source_file']}",
                data=source_path.read_bytes(),
                file_name=row["source_file"],
                mime="application/pdf",
                key=f"download_invoice_{row['id']}",
                icon=":material/download:",
                on_click="ignore",
            )
        else:
            st.caption(f"Bestand {row['source_file']} niet gevonden in de invoices- of pdfs-map.")
    else:
        st.caption("Selecteer een factuur om de pdf te downloaden.")
else:
    st.caption("Nog geen facturen geïmporteerd.")
