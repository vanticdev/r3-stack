"""Requirement values: parsing and matching.

A ``source.requires`` value is one of:

* a quantity (``2.4 GHz``, ``5 V``, a number): :class:`Approx`, matched within
  ``APPROX_TOLERANCE`` (relative);
* a two-element list of quantities (``[4.5 V, 5.5 V]``): :class:`Range`;
* a comparison string (``">= 4 GB"``, ``"< 60 g"``): :class:`Bound`;
* a tagged size (``12 mm hex``): :class:`Tagged`, a quantity plus a shape word;
* a plain string or list of strings (``crsf``, ``[crsf, sbus]``): :class:`Equals`.

Quantities use :mod:`r3_model.units` plus data sizes (``B`` .. ``TB``, decimal),
``dBm``/``dBi``/``dB`` (kept as numbers) and money (``25 USD``, ``$25``).

Candidate values come as SI floats, ``[min, max]`` ranges, strings, string lists,
booleans or ``{"size", "shape"}`` dicts. A requirement *range* against a candidate
*range* asks for coverage: an operating range (``supply``) must contain the
whole required range. Against a single value it asks for inclusion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from r3_model.units import to_si

APPROX_TOLERANCE = 0.02
_DATA = {"B": 1.0, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}
_LOG_UNITS = {"dBm", "dBi", "dB"}
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_QUANTITY = re.compile(rf"^\s*({_NUMBER})\s*([A-Za-z/0-9]*)\s*$")
_TAGGED = re.compile(rf"^\s*({_NUMBER})\s*([A-Za-z]+)\s+([A-Za-z][A-Za-z_ -]*)\s*$")
_BOUND = re.compile(r"^\s*(>=|<=|>|<|=)\s*(.+)$")
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_MONEY = re.compile(rf"^\s*(?:([$€£¥])\s*({_NUMBER})|({_NUMBER})\s*([A-Za-z]{{3}})|([A-Za-z]{{3}})\s*({_NUMBER}))\s*$")

MatchStatus = Literal["pass", "fail", "unknown"]


def quantity(value: Any) -> float:
    """SI float of a number or ``"<number> <unit>"`` (data sizes and dB units included)."""
    if isinstance(value, str):
        match = _QUANTITY.match(value)
        if match and match.group(2) in _DATA:
            return float(match.group(1)) * _DATA[match.group(2)]
        if match and match.group(2) in _LOG_UNITS:
            return float(match.group(1))
    return to_si(value)


def is_quantity(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        quantity(value)
    except (ValueError, TypeError):
        return False
    return True


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str = "USD"

    def to_json(self) -> dict[str, Any]:
        return {"amount": round(self.amount, 4), "currency": self.currency}


def parse_money(value: Any, default_currency: str = "USD") -> Money | None:
    """``25``, ``"25 USD"``, ``"USD 25"``, ``"$25.50"``, ``{"amount", "currency"}`` -> Money."""
    if value is None or value == "":
        return None
    if isinstance(value, dict) and "amount" in value:
        return Money(float(value["amount"]), str(value.get("currency") or default_currency).upper())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Money(float(value), default_currency)
    if isinstance(value, str):
        match = _MONEY.match(value.replace(",", ""))
        if match:
            symbol, n1, n2, code2, code3, n3 = match.groups()
            if symbol:
                return Money(float(n1), _CURRENCY_SYMBOLS[symbol])
            if n2:
                return Money(float(n2), code2.upper())
            return Money(float(n3), code3.upper())
    raise ValueError(f"cannot parse price {value!r}; use e.g. '25 USD' or '$25'")


def normalize(text: Any) -> str:
    """Case- and separator-insensitive form: ``Raspberry Pi 5`` == ``raspberry-pi-5``."""
    return re.sub(r"[^a-z0-9.]+", "-", str(text).strip().lower()).strip("-")


# ------------------------------------------------------------------ constraints
@dataclass(frozen=True)
class Constraint:
    text: str

    def check(self, value: Any) -> tuple[MatchStatus, str]:
        raise NotImplementedError


def _as_range(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        low, high = float(value[0]), float(value[1])
        return (min(low, high), max(low, high))
    return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("size"), (int, float)):
        return float(value["size"])
    return None


@dataclass(frozen=True)
class Approx(Constraint):
    target: float
    tolerance: float = APPROX_TOLERANCE

    def check(self, value: Any) -> tuple[MatchStatus, str]:
        span = _as_range(value)
        slack = abs(self.target) * self.tolerance
        if span is not None:
            ok = span[0] - slack <= self.target <= span[1] + slack
            return ("pass" if ok else "fail"), f"{self.text} {'within' if ok else 'outside'} [{span[0]:g}, {span[1]:g}]"
        number = _as_number(value)
        if number is None:
            return "unknown", f"not a quantity: {value!r}"
        ok = abs(number - self.target) <= slack
        return ("pass" if ok else "fail"), f"{number:g} {'~=' if ok else '!='} {self.target:g} (±{self.tolerance:.0%})"


@dataclass(frozen=True)
class Range(Constraint):
    low: float
    high: float

    def check(self, value: Any) -> tuple[MatchStatus, str]:
        span = _as_range(value)
        if span is not None:
            # An operating range must cover the whole required range.
            ok = span[0] <= self.low * (1 + 1e-9) and span[1] >= self.high * (1 - 1e-9)
            return ("pass" if ok else "fail"), f"[{span[0]:g}, {span[1]:g}] {'covers' if ok else 'does not cover'} [{self.low:g}, {self.high:g}]"
        number = _as_number(value)
        if number is None:
            return "unknown", f"not a quantity: {value!r}"
        ok = self.low * (1 - 1e-9) <= number <= self.high * (1 + 1e-9)
        return ("pass" if ok else "fail"), f"{number:g} {'in' if ok else 'not in'} [{self.low:g}, {self.high:g}]"


@dataclass(frozen=True)
class Bound(Constraint):
    op: str
    limit: float

    def check(self, value: Any) -> tuple[MatchStatus, str]:
        span = _as_range(value)
        if span is not None:
            # Conservative: every point of the candidate's range must satisfy the bound.
            number = span[0] if self.op in (">", ">=") else span[1]
        else:
            number = _as_number(value)
        if number is None:
            return "unknown", f"not a quantity: {value!r}"
        ok = {">": number > self.limit, ">=": number >= self.limit * (1 - 1e-9), "<": number < self.limit,
              "<=": number <= self.limit * (1 + 1e-9), "=": abs(number - self.limit) <= abs(self.limit) * APPROX_TOLERANCE}[self.op]
        return ("pass" if ok else "fail"), f"{number:g} {self.op} {self.limit:g}: {'yes' if ok else 'no'}"


@dataclass(frozen=True)
class Tagged(Constraint):
    size: float
    shape: str

    def check(self, value: Any) -> tuple[MatchStatus, str]:
        if isinstance(value, dict):
            shape, size = value.get("shape"), value.get("size")
        elif isinstance(value, str):
            try:
                parsed = parse_constraint(value)
            except ValueError:
                return "unknown", f"cannot read {value!r}"
            if not isinstance(parsed, Tagged):
                return "unknown", f"cannot read {value!r} as <size> <shape>"
            shape, size = parsed.shape, parsed.size
        else:
            return "unknown", f"not a tagged size: {value!r}"
        if shape is None or size is None:
            return "unknown", f"incomplete: {value!r}"
        same_shape = normalize(shape) == normalize(self.shape)
        same_size = abs(float(size) - self.size) <= self.size * APPROX_TOLERANCE
        ok = same_shape and same_size
        return ("pass" if ok else "fail"), f"{float(size) * 1e3:g} mm {shape} {'matches' if ok else 'differs from'} {self.text}"


@dataclass(frozen=True)
class Equals(Constraint):
    options: tuple[str, ...]

    def check(self, value: Any) -> tuple[MatchStatus, str]:
        if value is None:
            return "unknown", "no value"
        if isinstance(value, bool):
            wanted = {o in ("true", "yes", "1") for o in self.options}
            ok = value in wanted
            return ("pass" if ok else "fail"), f"{value} vs {self.text}"
        values = [normalize(v) for v in (value if isinstance(value, (list, tuple)) else [value])]
        ok = any(v in self.options for v in values)
        return ("pass" if ok else "fail"), f"{', '.join(values)} {'matches' if ok else 'does not match'} {self.text}"


def parse_constraint(value: Any) -> Constraint:
    """Parse one ``requires`` value (see the module docstring)."""
    text = str(value)
    if isinstance(value, bool):
        return Equals(text, ("true",) if value else ("false",))
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(is_quantity(v) for v in value):
            low, high = sorted(quantity(v) for v in value)
            return Range(f"[{value[0]}, {value[1]}]", low, high)
        if all(isinstance(v, str) for v in value):
            return Equals(text, tuple(normalize(v) for v in value))
        raise ValueError(f"cannot parse requirement {value!r}: a list must be [min, max] quantities or strings")
    if isinstance(value, (int, float)):
        return Approx(text, float(value))
    if isinstance(value, str):
        bound = _BOUND.match(value)
        if bound:
            return Bound(text, bound.group(1), quantity(bound.group(2)))
        if is_quantity(value):
            return Approx(text, quantity(value))
        tagged = _TAGGED.match(value)
        if tagged:
            try:
                size = quantity(f"{tagged.group(1)} {tagged.group(2)}")
            except ValueError:
                pass
            else:
                return Tagged(text, size, tagged.group(3).strip())
        return Equals(text, (normalize(value),))
    raise ValueError(f"cannot parse requirement {value!r}")


_RANGE_SPLIT = re.compile(r"\s*(?:~|–|—|\bto\b|\.\.|(?<=[A-Za-z\d])\s*-\s*(?=\d))\s*")


def _with_unit(first: str, second: str) -> str:
    """``("4.5", "5.5 V")`` -> ``"4.5 V"``: a bare range start takes the end's unit."""
    start, end = _QUANTITY.match(first), _QUANTITY.match(second)
    if start and end and not start.group(2) and end.group(2):
        return f"{first} {end.group(2)}"
    return first


def parse_value(text: Any) -> Any:
    """A listing or datasheet value string -> candidate value.

    ``"4.5V ~ 5.5V"`` / ``"4.5 V to 5.5 V"`` / ``"4.5-5.5V"`` -> ``[4.5, 5.5]``; ``"110 mm"`` -> ``0.11``;
    ``"12 mm hex"`` -> ``{"size": 0.012, "shape": "hex"}``; ``"yes"`` -> True; ``"3.3V, 5V"`` -> ``[3.3, 5.0]``
    is NOT a range: comma lists become lists. Anything else stays a (stripped) string."""
    if isinstance(text, (int, float, bool)) or text is None or isinstance(text, dict):
        return text
    if isinstance(text, (list, tuple)):
        return [parse_value(item) for item in text]
    value = str(text).strip()
    if value.lower() in ("yes", "true"):
        return True
    if value.lower() in ("no", "false"):
        return False
    if is_quantity(value):
        return quantity(value)
    parts = [p for p in _RANGE_SPLIT.split(value) if p]
    if len(parts) == 2 and is_quantity(parts[1]) and is_quantity(_with_unit(parts[0], parts[1])):
        return sorted([quantity(_with_unit(parts[0], parts[1])), quantity(parts[1])])
    tagged = _TAGGED.match(value)
    if tagged:
        try:
            return {"size": quantity(f"{tagged.group(1)} {tagged.group(2)}"), "shape": normalize(tagged.group(3))}
        except ValueError:
            pass
    if "," in value:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if len(items) > 1 and all(is_quantity(item) for item in items):
            return [quantity(item) for item in items]
    return value
