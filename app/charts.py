"""Chart geometry for server-drawn SVG charts (no JavaScript chart library, CSP-friendly).

These functions only position real values on a canvas; they never invent or change data."""
import math

SLUG = {"Very Low": "very-low", "Low": "low", "Moderate": "moderate", "High": "high", "Critical": "critical"}


def bar_rows(items, max_value=None):
    """items: [(label, value, css_class)] -> rows with a 0-100 width for an SVG bar."""
    values = [v for _, v, _ in items]
    top = max_value if max_value is not None else (max(values) if values else 0)
    rows = []
    for label, value, css in items:
        pct = 0 if not top else max(0.0, min(100.0, 100.0 * value / top))
        rows.append({"label": label, "value": value, "pct": round(pct, 2), "css": css})
    return rows


def stack(segments):
    """segments: [(label, value, css_class)] -> pieces with x/width on a 0-100 axis."""
    total = sum(v for _, v, _ in segments)
    x, pieces = 0.0, []
    for label, value, css in segments:
        width = 0 if not total else 100.0 * value / total
        pieces.append({"label": label, "value": value, "x": round(x, 3), "w": round(width, 3), "css": css,
                       "share": 0 if not total else round(100.0 * value / total, 1)})
        x += width
    return {"total": total, "pieces": pieces}


def _nice_log_ticks(lo, hi):
    ticks = []
    for exp in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
        for m in (1, 3):
            v = m * 10 ** exp
            if lo <= v <= hi:
                ticks.append(v)
    return ticks


def scatter_log(series, width=560, height=380, pad=(58, 18, 22, 48)):
    """Actual vs predicted on log-log axes. series: {name: [(actual, predicted), ...]} (values > 0)."""
    left, right, top, bottom = pad
    values = [v for pts in series.values() for p in pts for v in p if v > 0]
    lo = 10 ** math.floor(math.log10(min(values)))
    hi = 10 ** math.ceil(math.log10(max(values)))
    span = math.log10(hi) - math.log10(lo)
    w, h = width - left - right, height - top - bottom

    def sx(v):
        return round(left + w * (math.log10(v) - math.log10(lo)) / span, 1)

    def sy(v):
        return round(top + h - h * (math.log10(v) - math.log10(lo)) / span, 1)

    points = {name: [{"x": sx(a), "y": sy(p)} for a, p in pts if a > 0 and p > 0] for name, pts in series.items()}
    ticks = [{"v": t, "x": sx(t), "y": sy(t), "label": (f"{t:g}")} for t in _nice_log_ticks(lo, hi)]
    return {"width": width, "height": height, "left": left, "right": width - right, "top": top,
            "bottom": height - bottom, "points": points, "ticks": ticks,
            "diag": {"x1": sx(lo), "y1": sy(lo), "x2": sx(hi), "y2": sy(hi)}}


def line(values, labels, highlight=None, width=560, height=300, pad=(64, 18, 18, 44)):
    """Simple line chart (used for the Elbow Method)."""
    left, right, top, bottom = pad
    hi = max(values) * 1.05
    w, h = width - left - right, height - top - bottom
    n = len(values)

    def sx(i):
        return round(left + (w * i / (n - 1) if n > 1 else w / 2), 1)

    def sy(v):
        return round(top + h - h * v / hi, 1)

    pts = [{"x": sx(i), "y": sy(v), "label": labels[i], "value": v, "hi": labels[i] == highlight}
           for i, v in enumerate(values)]
    step = 10 ** math.floor(math.log10(hi / 4)) if hi > 0 else 1
    step = step * (2 if hi / step > 8 else 1)
    yticks = [{"v": v, "y": sy(v)} for v in range(0, int(hi) + 1, int(step))]
    return {"width": width, "height": height, "left": left, "right": width - right, "top": top,
            "bottom": height - bottom, "points": pts, "path": " ".join(f"{p['x']},{p['y']}" for p in pts),
            "yticks": yticks}


def diverging(items, width_pct=50):
    """Signed values (e.g. % effects) -> rows with bar start/width on a 0-100 axis centred at 50."""
    top = max((abs(v) for _, v in items), default=0) or 1
    rows = []
    for label, value in items:
        w = width_pct * abs(value) / top
        rows.append({"label": label, "value": value, "x": round(50 - w if value < 0 else 50, 2), "w": round(w, 2),
                     "css": "fill-neg" if value < 0 else "fill-pos"})
    return rows


def heat_class(value, max_value):
    """Confusion-matrix cell shade 0-5."""
    if not value or not max_value:
        return "cm-0"
    return f"cm-{min(5, 1 + int(4 * value / max_value))}"
