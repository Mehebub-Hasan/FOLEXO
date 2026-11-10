"""Static FX conversion for dashboard aggregation (PLACEHOLDER).

The MVP schema has no exchange-rate table (docs/PRD.md §11 defers FX
handling), but a base-currency spend number is required for the dashboard.
These indicative USD-based rates make that number honest about its source;
replace with a real `exchange_rates` table + daily sync in a later phase and
keep the response's `unpriced_currencies` field as the transparency valve.
"""

import decimal
from decimal import Decimal

# Indicative rates → USD. Extend deliberately; unknown currencies are
# reported as unpriced rather than guessed.
FX_TO_USD: dict[str, Decimal] = {
    "USD": Decimal("1.0000"),
    "EUR": Decimal("1.0800"),
    "GBP": Decimal("1.2700"),
}

_TWO_PLACES = Decimal("0.01")


def convert_to_base(
    amount: Decimal, currency: str | None, base_currency: str
) -> Decimal | None:
    """Convert `amount` into `base_currency` via USD cross rates.

    A NULL/missing currency is treated as the base currency (invoices
    extracted without a currency code are assumed local). Returns None when
    a known currency has no rate (caller reports it as unpriced instead of
    guessing).
    """
    if amount is None:
        return None
    source = (currency or base_currency).upper()
    base = base_currency.upper()
    rate_from = FX_TO_USD.get(source)
    rate_to = FX_TO_USD.get(base)
    if rate_from is None or rate_to is None or rate_to == 0:
        return None
    try:
        converted = amount * rate_from / rate_to
    except (decimal.InvalidOperation, ValueError):
        return None
    return converted.quantize(_TWO_PLACES)
