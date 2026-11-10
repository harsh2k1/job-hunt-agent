from typing import Any


# One wording for every source, e.g. "GBP 32000-38000 yearly", so salaries
# read the same in the sheet and the dashboard whatever board they came from.
def format_salary_range(
    currency: str | None, low: float | None, high: float | None, interval: str | None
) -> str | None:
    amounts = [f"{amount:.0f}" for amount in (low, high) if amount]
    if not amounts:
        return None
    amount_range = amounts[0] if len(set(amounts)) == 1 else "-".join(amounts)
    parts: list[Any] = [currency, amount_range, interval]
    return " ".join(str(part) for part in parts if part)
