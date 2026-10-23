"""Trade-owned sidecar for the sandbox margin rule (the ONE margin rule; D306).

`FundManager.calculate_margin_required` is the single place a margin is computed. The sandbox reserves
it, and the simulator's `/margincalculator` estimate (`broker/stock_simulator/api/margin_api.py`, which
the risk gate and the nautilus preflight read) calls the same function, so the two cannot disagree.

Paper-trading approximation, NOT SPAN. Per product class:
- equity / futures / long option: traded value / leverage (`equity_*_leverage`, `futures_leverage`,
  `option_buy_leverage`). A long option therefore costs its premium at the default leverage of 1.
- short option: max(premium, strike x quantity / `option_sell_notional_leverage`). Default 16 = 6.25% of
  the strike notional (about 1.1 lakh for one NIFTY lot at 24000 x 75). Tune it in the sandbox
  Leverage page (or `sandbox_config.option_sell_notional_leverage`); higher blocks less.
  ponytail: no SPAN scan risk, no hedge offsets between legs; a spread blocks as two naked shorts.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class MarginRuleError(ValueError):
    """The margin cannot be priced (D410: fail loud, never a zero or a floor)."""


def _finite_decimal(value, what: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise MarginRuleError(f"{what} {value!r} is not a number") from None
    if not out.is_finite():
        raise MarginRuleError(f"{what} {value!r} is not finite")
    return out


def positive_price(price) -> Decimal:
    """`price` as a finite Decimal > 0, else MarginRuleError (NaN, inf, zero, negative, junk)."""
    out = _finite_decimal(price, "price")
    if out <= 0:
        raise MarginRuleError(f"price {price!r} must be above zero")
    return out


def short_option_margin(strike, quantity: int, premium_value: Decimal, notional_leverage) -> Decimal:
    """Strike notional / leverage, never below the premium value."""
    strike_d = _finite_decimal(strike, "strike")
    lev = _finite_decimal(notional_leverage, "option_sell_notional_leverage")
    if strike_d <= 0:
        raise MarginRuleError(f"short option has no usable strike ({strike!r})")
    if lev <= 0:
        raise MarginRuleError(f"option_sell_notional_leverage {notional_leverage!r} must be above zero")
    return max(premium_value, strike_d * quantity / lev)
