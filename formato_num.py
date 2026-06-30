"""Formato numérico argentino: miles con punto, decimales con coma."""

from __future__ import annotations

from typing import Any


def fmt_num(value: Any, decimals: int = 2) -> str:
    """Formatea un número con separador de miles (.) y decimales (,)."""
    if value is None or value == "":
        if decimals <= 0:
            return "0"
        return "0," + "0" * decimals
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        s = f"{n:,.0f}"
    else:
        s = f"{n:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_money(value: Any) -> str:
    return fmt_num(value, 2)
