"""Every number becomes text here, and only here. Decimal in, str out; ROUND_HALF_UP."""
import datetime
from decimal import Decimal, ROUND_HALF_UP

MINUS = "\u2212"            # MINUS SIGN, not a hyphen
UP, DOWN = "\u25b2", "\u25bc"
DOT = "\u00b7"
NDASH = "\u2013"
APPROX = "\u2248"
assert (ord(MINUS), ord(UP), ord(DOWN), ord(DOT), ord(NDASH), ord(APPROX)) == (
    0x2212, 0x25B2, 0x25BC, 0xB7, 0x2013, 0x2248)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def whole(value):
    """Decimal -> Decimal rounded to whole dollars, half up. Never returns negative zero."""
    q = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return q + 0 if q != 0 else Decimal("0")


def dollars(value):
    """'$1,234' / '−$45'. Whole dollars."""
    q = whole(value)
    return "%s$%s" % (MINUS if q < 0 else "", "{:,}".format(abs(int(q))))


def dollars_abs(value):
    """'$1,234' for the magnitude of an already-rounded figure."""
    return "$" + "{:,}".format(abs(int(whole(value))))


def cents(value):
    """'$41.75', but '$60' when there are no cents. For a bill's own amount."""
    q = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = MINUS if q < 0 else ""
    q = abs(q)
    if q == q.to_integral_value():
        return "%s$%s" % (sign, "{:,}".format(int(q)))
    return "%s$%s" % (sign, "{:,.2f}".format(q))


def month_day(d):
    """'Oct 2'."""
    return "%s %d" % (MONTHS[d.month - 1], d.day)


def age_text(seconds):
    """'just now' / '12m' / '3h' / '2d' — a duration, floor."""
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 48 * 3600:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def clock(dt):
    """'21:05' local."""
    return dt.strftime("%H:%M")


def month_span(first_month, last_month):
    """'Jan–Sep 2026' / 'Oct 2025–Sep 2026' / 'Sep 2026' from two (year, month) pairs."""
    (y1, m1), (y2, m2) = first_month, last_month
    if (y1, m1) == (y2, m2):
        return "%s %d" % (MONTHS[m1 - 1], y1)
    if y1 == y2:
        return "%s%s%s %d" % (MONTHS[m1 - 1], NDASH, MONTHS[m2 - 1], y1)
    return "%s %d%s%s %d" % (MONTHS[m1 - 1], y1, NDASH, MONTHS[m2 - 1], y2)


def days_in_month(year, month):
    nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - datetime.date(year, month, 1)).days
