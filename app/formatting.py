"""Display helpers. Indian number grouping: the last three digits, then groups of two (12,50,000)."""
from datetime import UTC, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India has no daylight saving; fixed offset is exact


def indian_grouping(whole_number):
    digits = str(abs(int(whole_number)))
    if len(digits) <= 3:
        grouped = digits
    else:
        head, tail = digits[:-3], digits[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        grouped = ",".join(pairs + [tail])
    return ("-" if int(whole_number) < 0 else "") + grouped


def format_inr(amount, paise=False):
    """format_inr(1250000) -> '₹12,50,000'; format_inr(304.15, paise=True) -> '₹304.15'."""
    if amount is None:
        return "Not available"
    if paise:
        cents = int(round(abs(float(amount)) * 100))
        whole, fraction = divmod(cents, 100)
        return f"{'-' if float(amount) < 0 else ''}₹{indian_grouping(whole)}.{fraction:02d}"
    value = int(round(float(amount)))
    return f"-₹{indian_grouping(-value)}" if value < 0 else f"₹{indian_grouping(value)}"



def format_number(value, decimals=0):
    """Quantities and counts with Indian grouping: format_number(123456.78, 1) -> '1,23,456.8'."""
    if value is None:
        return "-"
    value = round(float(value), decimals)
    whole = int(abs(value))
    text = indian_grouping(whole)
    if decimals:
        text += f"{abs(value) - whole:.{decimals}f}"[1:]
    return ("-" if value < 0 else "") + text


def format_ist(utc_naive):
    """Timestamps are stored in UTC; shown in Indian Standard Time."""
    if utc_naive is None:
        return "-"
    return utc_naive.replace(tzinfo=UTC).astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def safe_http_url(url):
    """Only http(s) links are rendered as links (blocks javascript: and similar URLs)."""
    return url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else None
