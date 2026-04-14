"""Generate verbose HTML invoices with full HWM math breakdown.

Each invoice shows:
- Previous HWM (adjusted for deposits/withdrawals)
- Current balance
- Profit calculation
- Fee breakdown
- Payment details (deposit address, network)

Outputs HTML files to data/invoices/{invoice_id}.html
Open in browser and print to PDF for clean results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from jinja2 import Template

logger = logging.getLogger(__name__)

_INVOICES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "invoices"

# ── Payment details ──

ODUM_COMPANY = {
    "name": "Odum Research Ltd",
    "email": "ikenna@odum-research.com",
    "address_line1": "9 Appold Street",
    "address_line2": "EC2A 2AP London",
    "region": "Greater London",
    "country": "United Kingdom",
}

ODUM_PAYMENT_INFO = {
    "company_name": "Odum Research Ltd",
    "network": "Ethereum (ERC-20)",
    "currency": "USDT",
    "deposit_address": "0x28B73A50B13e1CCc8c1059C12e15e737b7E348d9",
    "notes": "Please send exact amount. Include invoice ID in memo/reference.",
}

# Client company details (from Request Finance records)
CLIENT_DETAILS: dict[str, dict[str, str]] = {
    "PR": {
        "company_name": "Prismatic Multi-Strategy Master Fund",
        "contact_name": "Steven Slessor",
        "email": "info@prismaticcapital.com",
        "address": "British Virgin Islands",
    },
    "NN": {
        "company_name": "NamNar",
        "contact_name": "B. Namerow",
        "email": "bnamerow@namnarcap.com",
        "address": "",
    },
    "ET": {
        "company_name": "Eqvilent Trading Limited",
        "contact_name": "Andrey Vlasov",
        "email": "andrey.vlasov@eqvilent.com",
        "address": "Intershore Chambers, P.O.Box 4342, Road Town, Tortola, VG1110, British Virgin Islands",
    },
    "STD": {
        "company_name": "Steady Hash",
        "contact_name": "",
        "email": "villa@steadyhash.ai",
        "address": "",
    },
}

# ── Invoice data ──


@dataclass
class InvoiceLineItem:
    """Single line on an invoice."""

    description: str
    amount: Decimal


@dataclass
class InvoiceData:
    """All data needed to render an invoice."""

    invoice_id: str
    client_id: str
    client_name: str
    invoice_date: str
    due_date: str
    currency: str

    # HWM breakdown
    previous_hwm: Decimal
    deposits_since_hwm: Decimal
    withdrawals_since_hwm: Decimal
    effective_hwm: Decimal  # previous_hwm + net deposits
    current_balance: Decimal
    profit: Decimal

    # Fee
    fee_rate_pct: Decimal
    fee_label: str  # "Performance Fee (34%)"
    total_due: Decimal

    # Line items (fee + any extras)
    line_items: list[InvoiceLineItem]

    # Payment
    payment_info: dict[str, str]

    # Optional: introducer
    introducer_name: str = ""
    introducer_rate_pct: Decimal = Decimal("0")
    introducer_fee: Decimal = Decimal("0")

    # Bill-to details
    bill_to_company: str = ""
    bill_to_contact: str = ""
    bill_to_email: str = ""
    bill_to_address: str = ""

    # Status
    status: str = "ISSUED"


# ── HTML Template ──

_INVOICE_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ inv.invoice_id }} — {{ inv.client_name }}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    color: #1a1a2e;
    background: #f8f9fa;
    line-height: 1.6;
  }

  .invoice-container {
    max-width: 800px;
    margin: 40px auto;
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    overflow: hidden;
  }

  /* Header */
  .invoice-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #fff;
    padding: 48px 48px 36px;
    position: relative;
  }

  .invoice-header::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 4px;
    background: linear-gradient(90deg, #e94560, #0f3460, #e94560);
  }

  .header-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 32px;
  }

  .company-name {
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.5px;
  }

  .company-subtitle {
    font-size: 13px;
    color: rgba(255,255,255,0.6);
    margin-top: 4px;
  }

  .invoice-badge {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 8px;
    padding: 12px 20px;
    text-align: right;
  }

  .invoice-badge .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: rgba(255,255,255,0.6);
  }

  .invoice-badge .value {
    font-size: 18px;
    font-weight: 600;
    margin-top: 2px;
  }

  .header-meta {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 24px;
  }

  .meta-item .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: rgba(255,255,255,0.5);
  }

  .meta-item .value {
    font-size: 15px;
    font-weight: 500;
    margin-top: 2px;
  }

  /* Parties section */
  .parties {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 40px;
    padding: 32px 48px;
    border-bottom: 1px solid #e9ecef;
  }

  .party .party-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: #6c757d;
    font-weight: 600;
    margin-bottom: 12px;
  }

  .party .party-name {
    font-size: 15px;
    font-weight: 600;
    color: #1a1a2e;
    margin-bottom: 4px;
  }

  .party .party-detail {
    font-size: 13px;
    color: #495057;
    line-height: 1.5;
  }

  .status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .status-issued { background: #fff3cd; color: #856404; }
  .status-paid { background: #d4edda; color: #155724; }

  /* Body */
  .invoice-body { padding: 40px 48px; }

  .section { margin-bottom: 36px; }

  .section-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: #6c757d;
    font-weight: 600;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #f0f0f0;
  }

  /* HWM breakdown */
  .hwm-breakdown {
    background: #f8f9fa;
    border-radius: 8px;
    padding: 24px;
    border: 1px solid #e9ecef;
  }

  .hwm-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    font-size: 14px;
  }

  .hwm-row .label { color: #495057; }
  .hwm-row .value { font-weight: 500; font-variant-numeric: tabular-nums; }

  .hwm-row.subtotal {
    border-top: 1px solid #dee2e6;
    margin-top: 8px;
    padding-top: 12px;
  }

  .hwm-row.profit {
    border-top: 2px solid #1a1a2e;
    margin-top: 12px;
    padding-top: 14px;
    font-size: 16px;
    font-weight: 600;
  }

  .hwm-row.profit .value { color: #28a745; }
  .hwm-row.negative .value { color: #dc3545; }

  .calculation {
    font-size: 13px;
    color: #6c757d;
    margin-top: 4px;
    font-style: italic;
  }

  /* Line items table */
  .items-table {
    width: 100%;
    border-collapse: collapse;
  }

  .items-table th {
    text-align: left;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #6c757d;
    padding: 12px 0;
    border-bottom: 2px solid #1a1a2e;
  }

  .items-table th:last-child { text-align: right; }

  .items-table td {
    padding: 14px 0;
    font-size: 14px;
    border-bottom: 1px solid #f0f0f0;
  }

  .items-table td:last-child {
    text-align: right;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }

  .items-table .total-row td {
    border-top: 2px solid #1a1a2e;
    border-bottom: none;
    padding-top: 16px;
    font-size: 18px;
    font-weight: 700;
  }

  /* Payment details */
  .payment-box {
    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
    border-radius: 8px;
    padding: 24px;
    border: 1px solid #dee2e6;
  }

  .payment-row {
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    font-size: 14px;
  }

  .payment-row .label {
    color: #6c757d;
    font-weight: 500;
  }

  .payment-row .value {
    font-weight: 500;
    word-break: break-all;
  }

  .address-value {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 13px;
    background: #fff;
    padding: 8px 12px;
    border-radius: 6px;
    border: 1px solid #dee2e6;
    margin-top: 8px;
    word-break: break-all;
    letter-spacing: 0.3px;
  }

  .payment-note {
    font-size: 12px;
    color: #6c757d;
    margin-top: 12px;
    font-style: italic;
  }

  /* Footer */
  .invoice-footer {
    padding: 24px 48px;
    background: #f8f9fa;
    border-top: 1px solid #e9ecef;
    font-size: 12px;
    color: #6c757d;
    text-align: center;
  }

  /* Print styles */
  @media print {
    body { background: #fff; }
    .invoice-container {
      box-shadow: none;
      margin: 0;
      border-radius: 0;
    }
  }
</style>
</head>
<body>
<div class="invoice-container">

  <!-- Header -->
  <div class="invoice-header">
    <div class="header-top">
      <div>
        <div class="company-name">{{ inv.payment_info.company_name }}</div>
        <div class="company-subtitle">Digital Asset Management</div>
      </div>
      <div class="invoice-badge">
        <div class="label">Invoice</div>
        <div class="value">{{ inv.invoice_id }}</div>
      </div>
    </div>
    <div class="header-meta">
      <div class="meta-item">
        <div class="label">Bill To</div>
        <div class="value">{{ inv.client_name }}</div>
      </div>
      <div class="meta-item">
        <div class="label">Invoice Date</div>
        <div class="value">{{ inv.invoice_date }}</div>
      </div>
      <div class="meta-item">
        <div class="label">Status</div>
        <div class="value">
          <span class="status-badge status-{{ inv.status|lower }}">{{ inv.status }}</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Parties -->
  <div class="parties">
    <div class="party">
      <div class="party-label">From</div>
      <div class="party-name">{{ odum.name }}</div>
      <div class="party-detail">{{ odum.email }}</div>
      <div class="party-detail">{{ odum.address_line1 }}</div>
      <div class="party-detail">{{ odum.address_line2 }}</div>
      <div class="party-detail">{{ odum.region }}, {{ odum.country }}</div>
    </div>
    <div class="party">
      <div class="party-label">Billed To</div>
      <div class="party-name">{{ inv.bill_to_company or inv.client_name }}</div>
      {% if inv.bill_to_contact %}<div class="party-detail">{{ inv.bill_to_contact }}</div>{% endif %}
      {% if inv.bill_to_email %}<div class="party-detail">{{ inv.bill_to_email }}</div>{% endif %}
      {% if inv.bill_to_address %}<div class="party-detail">{{ inv.bill_to_address }}</div>{% endif %}
    </div>
  </div>

  <!-- Body -->
  <div class="invoice-body">

    <!-- HWM Breakdown -->
    <div class="section">
      <div class="section-title">Performance Calculation</div>
      <div class="hwm-breakdown">
        <div class="hwm-row">
          <span class="label">Previous High-Water Mark</span>
          <span class="value">{{ fmt(inv.previous_hwm) }} {{ inv.currency }}</span>
        </div>
        {% if inv.deposits_since_hwm > 0 %}
        <div class="hwm-row">
          <span class="label">+ Deposits since HWM</span>
          <span class="value">+{{ fmt(inv.deposits_since_hwm) }}</span>
        </div>
        {% endif %}
        {% if inv.withdrawals_since_hwm > 0 %}
        <div class="hwm-row">
          <span class="label">− Withdrawals since HWM</span>
          <span class="value">−{{ fmt(inv.withdrawals_since_hwm) }}</span>
        </div>
        {% endif %}
        {% if inv.deposits_since_hwm > 0 or inv.withdrawals_since_hwm > 0 %}
        <div class="hwm-row subtotal">
          <span class="label">Effective HWM (adjusted for transfers)</span>
          <span class="value">{{ fmt(inv.effective_hwm) }} {{ inv.currency }}</span>
        </div>
        {% endif %}
        <div class="hwm-row">
          <span class="label">Current Balance</span>
          <span class="value">{{ fmt(inv.current_balance) }} {{ inv.currency }}</span>
        </div>
        <div class="hwm-row profit">
          <span class="label">Profit above HWM</span>
          <span class="value">{{ fmt(inv.profit) }} {{ inv.currency }}</span>
        </div>
        <div class="calculation">
          {{ fmt(inv.current_balance) }} − {{ fmt(inv.effective_hwm) }} = {{ fmt(inv.profit) }}
        </div>
      </div>
    </div>

    <!-- Line Items -->
    <div class="section">
      <div class="section-title">Invoice Details</div>
      <table class="items-table">
        <thead>
          <tr>
            <th>Description</th>
            <th>Amount ({{ inv.currency }})</th>
          </tr>
        </thead>
        <tbody>
          {% for item in inv.line_items %}
          <tr>
            <td>{{ item.description }}</td>
            <td>{{ fmt(item.amount) }}</td>
          </tr>
          {% endfor %}
          <tr class="total-row">
            <td>Total Due</td>
            <td>{{ fmt(inv.total_due) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Payment Details -->
    <div class="section">
      <div class="section-title">Payment Details</div>
      <div class="payment-box">
        <div class="payment-row">
          <span class="label">Currency</span>
          <span class="value">{{ inv.payment_info.currency }}</span>
        </div>
        <div class="payment-row">
          <span class="label">Network</span>
          <span class="value">{{ inv.payment_info.network }}</span>
        </div>
        <div class="payment-row">
          <span class="label">Send to</span>
        </div>
        <div class="address-value">{{ inv.payment_info.deposit_address }}</div>
        <div class="payment-note">{{ inv.payment_info.notes }}</div>
      </div>
    </div>

  </div>

  <!-- Footer -->
  <div class="invoice-footer">
    {{ inv.payment_info.company_name }} &middot; Invoice {{ inv.invoice_id }} &middot; Generated {{ now }}
  </div>

</div>
</body>
</html>
""")


def _fmt(value: Decimal) -> str:
    """Format a Decimal to a human-readable string with commas."""
    # Round to 2 decimal places
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Format with commas
    parts = str(rounded).split(".")
    integer_part = parts[0]
    decimal_part = parts[1] if len(parts) > 1 else "00"

    # Handle negative
    negative = integer_part.startswith("-")
    if negative:
        integer_part = integer_part[1:]

    # Add thousand separators
    formatted = ""
    for i, digit in enumerate(reversed(integer_part)):
        if i > 0 and i % 3 == 0:
            formatted = "," + formatted
        formatted = digit + formatted

    result = f"${formatted}.{decimal_part}"
    if negative:
        result = f"-{result}"
    return result


def generate_invoice_html(inv: InvoiceData) -> str:
    """Render an invoice to HTML."""
    now = datetime.now(tz=UTC).strftime("%B %d, %Y %H:%M UTC")
    return _INVOICE_TEMPLATE.render(inv=inv, odum=ODUM_COMPANY, fmt=_fmt, now=now)


def _build_invoice(
    invoice_id: str,
    client_id: str,
    client_name: str,
    invoice_date: str,
    due_date: str,
    status: str,
    previous_hwm: Decimal,
    current_balance: Decimal,
    profit: Decimal,
    fee_rate_pct: Decimal,
    fee_label: str,
    deposits: Decimal = Decimal("0"),
    withdrawals: Decimal = Decimal("0"),
) -> InvoiceData:
    """Helper to build an InvoiceData with client details auto-filled."""
    effective = previous_hwm + deposits - withdrawals
    total_due = (profit * fee_rate_pct).quantize(Decimal("0.01"))
    cd = CLIENT_DETAILS.get(client_id, {})
    return InvoiceData(
        invoice_id=invoice_id,
        client_id=client_id,
        client_name=client_name,
        invoice_date=invoice_date,
        due_date=due_date,
        currency="USDT",
        previous_hwm=previous_hwm,
        deposits_since_hwm=deposits,
        withdrawals_since_hwm=withdrawals,
        effective_hwm=effective,
        current_balance=current_balance,
        profit=profit,
        fee_rate_pct=fee_rate_pct,
        fee_label=fee_label,
        total_due=total_due,
        line_items=[
            InvoiceLineItem(
                description=f"{fee_label} — {int(fee_rate_pct * 100)}% × {_fmt(profit)} profit above HWM",
                amount=total_due,
            ),
        ],
        payment_info=ODUM_PAYMENT_INFO,
        bill_to_company=cd.get("company_name", client_name),
        bill_to_contact=cd.get("contact_name", ""),
        bill_to_email=cd.get("email", ""),
        bill_to_address=cd.get("address", ""),
        status=status,
    )


def _build_trader_invoice(
    invoice_id: str,
    client_id: str,
    client_name: str,
    invoice_date: str,
    due_date: str,
    status: str,
    previous_hwm: Decimal,
    current_balance: Decimal,
    profit: Decimal,
    fee_rate_pct: Decimal,
) -> InvoiceData:
    """Build a trader payment invoice (Odum pays trader)."""
    total_due = (profit * fee_rate_pct).quantize(Decimal("0.01"))
    return InvoiceData(
        invoice_id=invoice_id,
        client_id=client_id,
        client_name=client_name,
        invoice_date=invoice_date,
        due_date=due_date,
        currency="USDT",
        previous_hwm=previous_hwm,
        deposits_since_hwm=Decimal("0"),
        withdrawals_since_hwm=Decimal("0"),
        effective_hwm=previous_hwm,
        current_balance=current_balance,
        profit=profit,
        fee_rate_pct=fee_rate_pct,
        fee_label="Trader Performance Fee (10%)",
        total_due=total_due,
        line_items=[
            InvoiceLineItem(
                description=f"Trader Fee — 10% × {_fmt(profit)} profit above trader HWM",
                amount=total_due,
            ),
        ],
        payment_info=ODUM_PAYMENT_INFO,
        bill_to_company="Trader",
        bill_to_contact="",
        bill_to_email="",
        bill_to_address="",
        status=status,
    )


def _build_introducer_invoice(
    invoice_id: str,
    client_id: str,
    introducer_name: str,
    invoice_date: str,
    due_date: str,
    status: str,
    odum_invoice_amount: Decimal,
    rate_pct: Decimal,
    introducer_details: dict[str, str],
) -> InvoiceData:
    """Build an introducer fee invoice."""
    total_due = (odum_invoice_amount * rate_pct).quantize(Decimal("0.01"))
    return InvoiceData(
        invoice_id=invoice_id,
        client_id=client_id,
        client_name=introducer_name,
        invoice_date=invoice_date,
        due_date=due_date,
        currency="USDT",
        previous_hwm=Decimal("0"),
        deposits_since_hwm=Decimal("0"),
        withdrawals_since_hwm=Decimal("0"),
        effective_hwm=Decimal("0"),
        current_balance=Decimal("0"),
        profit=odum_invoice_amount,
        fee_rate_pct=rate_pct,
        fee_label=f"Introducer Fee ({int(rate_pct * 100)}%)",
        total_due=total_due,
        line_items=[
            InvoiceLineItem(
                description=f"Introducer Fee — {int(rate_pct * 100)}% × {_fmt(odum_invoice_amount)} Odum invoice for {client_id}",
                amount=total_due,
            ),
        ],
        payment_info=ODUM_PAYMENT_INFO,
        bill_to_company=introducer_details.get("company_name", introducer_name),
        bill_to_contact=introducer_details.get("contact_name", ""),
        bill_to_email=introducer_details.get("email", ""),
        bill_to_address=introducer_details.get("address", ""),
        status=status,
    )


# Introducer company details
INTRODUCER_DETAILS: dict[str, dict[str, str]] = {
    "max": {
        "company_name": "Maxim Shilo",
        "contact_name": "Maxim Shilo",
        "email": "",
        "address": "",
    },
    "bluecoast": {
        "company_name": "Blue Coast Capital Partners",
        "contact_name": "Alban Gasser",
        "email": "info@bluecoastcp.com",
        "address": "20-22 Wenlock Road, London, N1 7GU, United Kingdom",
    },
}


def get_all_2026_invoices() -> list[InvoiceData]:
    """All 2026 invoices — odum, trader, introducer — Feb + April runs."""
    return [
        # ════════════════════════════════════════════════════
        # February 17, 2026 — Odum invoices
        # ════════════════════════════════════════════════════
        _build_invoice(
            "INV-2026-PR-001",
            "PR",
            "Prism Capital",
            "February 17, 2026",
            "March 19, 2026",
            "PAID",
            previous_hwm=Decimal("306837"),
            current_balance=Decimal("326380"),
            profit=Decimal("19543"),
            fee_rate_pct=Decimal("0.40"),
            fee_label="Performance Fee (40%)",
            withdrawals=Decimal("20000"),
        ),
        _build_invoice(
            "INV-2026-NN-001",
            "NN",
            "NamNar",
            "February 17, 2026",
            "March 19, 2026",
            "ISSUED",  # unpaid
            previous_hwm=Decimal("100000"),
            current_balance=Decimal("108400"),
            profit=Decimal("8400"),
            fee_rate_pct=Decimal("0.30"),
            fee_label="Performance Fee (30%)",
        ),
        _build_invoice(
            "INV-2026-ET-001",
            "ET",
            "Eqvilent",
            "February 17, 2026",
            "March 19, 2026",
            "PAID",
            previous_hwm=Decimal("500000"),
            current_balance=Decimal("519000"),
            profit=Decimal("19000"),
            fee_rate_pct=Decimal("0.30"),
            fee_label="Performance Fee (30%)",
        ),
        _build_invoice(
            "INV-2026-STD-001",
            "STD",
            "Steady Hash",
            "February 17, 2026",
            "March 19, 2026",
            "PAID",
            previous_hwm=Decimal("500000"),
            current_balance=Decimal("514800"),
            profit=Decimal("14800"),
            fee_rate_pct=Decimal("0.35"),
            fee_label="Performance Fee (35%)",
        ),
        # ── Feb 2026 — Trader payments ──
        _build_trader_invoice(
            "TRD-2026-PR-001",
            "PR",
            "Prism Capital",
            "February 17, 2026",
            "February 17, 2026",
            "PAID",
            previous_hwm=Decimal("316494"),
            current_balance=Decimal("326380"),
            profit=Decimal("9886"),
            fee_rate_pct=Decimal("0.10"),
        ),
        _build_trader_invoice(
            "TRD-2026-NN-001",
            "NN",
            "NamNar",
            "February 17, 2026",
            "February 17, 2026",
            "PAID",
            previous_hwm=Decimal("105070"),
            current_balance=Decimal("108400"),
            profit=Decimal("3330"),
            fee_rate_pct=Decimal("0.10"),
        ),
        _build_trader_invoice(
            "TRD-2026-ET-001",
            "ET",
            "Eqvilent",
            "February 17, 2026",
            "February 17, 2026",
            "PAID",
            previous_hwm=Decimal("503400"),
            current_balance=Decimal("519000"),
            profit=Decimal("15600"),
            fee_rate_pct=Decimal("0.10"),
        ),
        _build_trader_invoice(
            "TRD-2026-STD-001",
            "STD",
            "Steady Hash",
            "February 17, 2026",
            "February 17, 2026",
            "PAID",
            previous_hwm=Decimal("503400"),
            current_balance=Decimal("514800"),
            profit=Decimal("11400"),
            fee_rate_pct=Decimal("0.10"),
        ),
        # ── Feb 2026 — Introducer invoices ──
        _build_introducer_invoice(
            "INT-MAX-001",
            "PR",
            "Maxim Shilo",
            "February 20, 2026",
            "March 20, 2026",
            "PAID",
            odum_invoice_amount=Decimal("7817.21"),
            rate_pct=Decimal("0.15"),
            introducer_details=INTRODUCER_DETAILS["max"],
        ),
        # Blue Coast (ET) — Feb: 5% of $5,700 = $285 — NOT paid
        _build_introducer_invoice(
            "INT-BC-001",
            "ET",
            "Blue Coast Capital Partners",
            "February 25, 2026",
            "March 25, 2026",
            "ISSUED",
            odum_invoice_amount=Decimal("5700"),
            rate_pct=Decimal("0.05"),
            introducer_details=INTRODUCER_DETAILS["bluecoast"],
        ),
        # ════════════════════════════════════════════════════
        # April 9, 2026 — Odum invoices
        # ════════════════════════════════════════════════════
        _build_invoice(
            "INV-2026-PR-002",
            "PR",
            "Prism Capital",
            "April 9, 2026",
            "May 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("326380"),
            current_balance=Decimal("335070"),
            profit=Decimal("8690"),
            fee_rate_pct=Decimal("0.34"),
            fee_label="Performance Fee (34%)",
        ),
        _build_invoice(
            "INV-2026-NN-002",
            "NN",
            "NamNar",
            "April 9, 2026",
            "May 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("108400"),
            current_balance=Decimal("111986"),
            profit=Decimal("3586"),
            fee_rate_pct=Decimal("0.30"),
            fee_label="Performance Fee (30%)",
        ),
        _build_invoice(
            "INV-2026-ET-002",
            "ET",
            "Eqvilent",
            "April 9, 2026",
            "May 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("519000"),
            current_balance=Decimal("2018939"),
            profit=Decimal("18939"),
            fee_rate_pct=Decimal("0.30"),
            fee_label="Performance Fee (30%)",
            deposits=Decimal("1500000"),
            withdrawals=Decimal("19000"),
        ),
        _build_invoice(
            "INV-2026-STD-002",
            "STD",
            "Steady Hash",
            "April 9, 2026",
            "May 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("514800"),
            current_balance=Decimal("1012861"),
            profit=Decimal("12739"),
            fee_rate_pct=Decimal("0.35"),
            fee_label="Performance Fee (35%)",
            deposits=Decimal("500122"),
            withdrawals=Decimal("14800"),
        ),
        # ── Apr 2026 — Trader payments ──
        _build_trader_invoice(
            "TRD-2026-PR-002",
            "PR",
            "Prism Capital",
            "April 9, 2026",
            "April 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("326380"),
            current_balance=Decimal("335070"),
            profit=Decimal("8690"),
            fee_rate_pct=Decimal("0.10"),
        ),
        _build_trader_invoice(
            "TRD-2026-NN-002",
            "NN",
            "NamNar",
            "April 9, 2026",
            "April 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("108400"),
            current_balance=Decimal("111986"),
            profit=Decimal("3586"),
            fee_rate_pct=Decimal("0.10"),
        ),
        _build_trader_invoice(
            "TRD-2026-ET-002",
            "ET",
            "Eqvilent",
            "April 9, 2026",
            "April 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("519000"),
            current_balance=Decimal("2018939"),
            profit=Decimal("18939"),
            fee_rate_pct=Decimal("0.10"),
        ),
        _build_trader_invoice(
            "TRD-2026-STD-002",
            "STD",
            "Steady Hash",
            "April 9, 2026",
            "April 9, 2026",
            "ISSUED",
            previous_hwm=Decimal("514800"),
            current_balance=Decimal("1012861"),
            profit=Decimal("12739"),
            fee_rate_pct=Decimal("0.10"),
        ),
        # ── Apr 2026 — Introducer invoices ──
        _build_introducer_invoice(
            "INT-MAX-002",
            "PR",
            "Maxim Shilo",
            "April 9, 2026",
            "May 9, 2026",
            "ISSUED",
            odum_invoice_amount=Decimal("2954.60"),
            rate_pct=Decimal("0.15"),
            introducer_details=INTRODUCER_DETAILS["max"],
        ),
        _build_introducer_invoice(
            "INT-BC-002",
            "ET",
            "Blue Coast Capital Partners",
            "April 9, 2026",
            "May 9, 2026",
            "ISSUED",
            odum_invoice_amount=Decimal("5681.70"),
            rate_pct=Decimal("0.05"),
            introducer_details=INTRODUCER_DETAILS["bluecoast"],
        ),
    ]


def generate_all_invoices() -> list[str]:
    """Generate all 2026 invoice HTMLs and write to data/invoices/.

    Returns list of file paths written.
    """
    _INVOICES_DIR.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    for inv in get_all_2026_invoices():
        html = generate_invoice_html(inv)
        path = _INVOICES_DIR / f"{inv.invoice_id}.html"
        path.write_text(html, encoding="utf-8")
        paths.append(str(path))
        logger.info("Generated: %s [%s] -> %s", inv.invoice_id, inv.status, path)

    return paths


def get_invoice_html_path(invoice_id: str) -> Path | None:
    """Get path to a generated invoice HTML, or None if not found."""
    path = _INVOICES_DIR / f"{invoice_id}.html"
    return path if path.exists() else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generated = generate_all_invoices()
    for p in generated:
        print(f"  {p}")
    print(f"\nGenerated {len(generated)} invoices. Open in browser to preview/print to PDF.")
