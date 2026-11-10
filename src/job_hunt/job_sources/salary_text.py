from typing import Any


# One wording for every source, e.g. "GBP 32000-38000 yearly", so salaries
# read the same in the sheet and the dashboard whatever board they came from.
# Amounts can come as numbers or as text ("50000.00"); zero or unreadable
# amounts count as not given.
def format_salary_range(currency: str | None, low: Any, high: Any, interval: str | None) -> str | None:
    amounts = [f"{amount:.0f}" for amount in map(_to_amount, (low, high)) if amount]
    if not amounts:
        return None
    amount_range = amounts[0] if len(set(amounts)) == 1 else "-".join(amounts)
    return " ".join(str(part) for part in (currency, amount_range, interval) if part)


def _to_amount(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
