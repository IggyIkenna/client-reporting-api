import base64
import io
import logging
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _load_jinja_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


matplotlib.use("Agg")


def generate_chart_base64(monthly_returns: list[float], labels: list[str]) -> str:
    """Generate a base64-encoded chart from monthly returns data."""
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ["green" if r >= 0 else "red" for r in monthly_returns]
    ax.bar(labels, monthly_returns, color=colors)
    ax.set_xlabel("Period")
    ax.set_ylabel("Return %")
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.5)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def render_executive_summary(
    client_id: str,
    period_month: str,
    closing_aum: float,
    return_pct: float,
    currency: str,
    chart_base64: str,
    ai_summary: str | None = None,
) -> str:
    """Render the executive summary HTML template."""
    env = _load_jinja_env()
    template = env.get_template("odum_executive_summary.html")
    return template.render(
        client_id=client_id,
        period_month=period_month,
        closing_aum=closing_aum,
        return_pct=return_pct,
        currency=currency,
        chart_base64=chart_base64,
        ai_summary=ai_summary,
    )


def render_btc_investor_note(
    client_id: str,
    period_month: str,
    monthly_return: float,
    inception_return: float,
    start_balance: float,
    end_balance: float,
    currency: str,
    inception_date: str | None = None,
    monthly_returns: list[dict[str, str]] | None = None,
) -> str:
    """Render the BTC investor note HTML template."""
    env = _load_jinja_env()
    template = env.get_template("btc_investor_note.html")

    monthly_return_str = (
        f"+{monthly_return:.2f}%" if monthly_return >= 0 else f"{monthly_return:.2f}%"
    )
    inception_return_str = (
        f"+{inception_return:.2f}%" if inception_return >= 0 else f"{inception_return:.2f}%"
    )

    return template.render(
        client_id=client_id,
        period_month=period_month,
        monthly_return=monthly_return,
        inception_return=inception_return,
        monthly_return_str=monthly_return_str,
        inception_return_str=inception_return_str,
        start_balance=start_balance,
        end_balance=end_balance,
        currency=currency,
        inception_date=inception_date,
        monthly_returns=monthly_returns,
    )
