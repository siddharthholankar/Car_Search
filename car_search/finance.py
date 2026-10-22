"""Loan payment and rough resale estimates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Payment:
    amount_financed: float
    monthly_payment: float
    total_interest: float
    total_cost: float  # everything paid: down payment + all monthly payments


def monthly_payment(price: float, down_payment: float = 0.0, apr: float = 7.0, term_months: int = 60,
                    tax_rate: float = 0.0945, fees: float = 0.0, trade_in: float = 0.0) -> Payment:
    """Standard amortized loan payment. `apr` and `tax_rate` are percent and fraction respectively.

    Default tax_rate is roughly the combined Louisiana state + Rapides Parish rate; set your own.
    Sales tax is applied to the price less trade-in, as most states do.
    """
    taxable = max(price - trade_in, 0)
    financed = max(price + taxable * tax_rate + fees - down_payment - trade_in, 0)
    r = apr / 100 / 12
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    pay = financed / term_months if r == 0 else financed * r / (1 - (1 + r) ** -term_months)
    total_paid = pay * term_months
    return Payment(
        amount_financed=round(financed, 2),
        monthly_payment=round(pay, 2),
        total_interest=round(total_paid - financed, 2),
        total_cost=round(total_paid + down_payment, 2),
    )


def resale_estimate(price: float, years: int, annual_depreciation: float = 0.12) -> float:
    """Value after `years` at a flat yearly depreciation rate (~10-15% is typical for these cars)."""
    return round(price * (1 - annual_depreciation) ** years, -2)
