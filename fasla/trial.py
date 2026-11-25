"""Try Fasla without an account: upload an orders file, get a plan per style and size.

The file is kept (scrubbed of customer details) to improve how Fasla reads files and forecasts, as the
privacy policy says. Plans run in the background; the page polls for the result.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

import pandas as pd

from brandopt.view import build_view

from .planner import WorkbookError, apply_overrides, clean_settings, read_workbook, suggest_settings

MAX_STYLES_SHOWN = 30


def _default_costs(products: pd.DataFrame) -> dict:
    """Styles without a cost get 45% of their price, the same guess the app starts from."""
    p = products.copy()
    cost = pd.to_numeric(p.unit_cost, errors="coerce")
    price = pd.to_numeric(p.price, errors="coerce")
    p["guess"] = cost.fillna(price * 0.45)
    out = {}
    for sid, g in p.groupby("style_id"):
        if cost[g.index].isna().all():
            v = float(g.guess.median()) if g.guess.notna().any() else None
            if v and v > 0:
                out[str(sid)] = round(v, 2)
    return out


def plan_file(data: bytes, time_limit_s: int = 10, mapping: dict | None = None) -> dict:
    """Plan an uploaded file with sensible defaults. Returns a small, display-ready result."""
    t0 = time.time()
    parsed = read_workbook(data, mapping=mapping)
    inp = parsed["inp"]
    st = dict(parsed.get("settings") or {})
    if inp.get("mode") == "orders" and "orders" in inp:
        st.update(suggest_settings(inp["products"], inp["orders"]))
    costs = _default_costs(inp["products"])
    if costs:
        st["costs"] = costs
    inp["settings"] = clean_settings(st, parsed.get("settings") or {})
    apply_overrides(inp, inp["settings"])
    view, _ = build_view(inp, 60, (1.0,), time_limit_s, "Your brand")
    base = view["variants"][view["base"]]

    by_style = defaultdict(lambda: {"sizes": defaultdict(int), "colors": defaultdict(int), "units": 0})
    for row, q in zip(view["sku"], base["Q"]):
        q = int(round(q or 0))
        s = by_style[row["s"]]
        s["sizes"][str(row["z"])] += q
        s["colors"][str(row["c"])] += q
        s["units"] += q
    order = [str(z) for z in view.get("sizes") or []]
    fc = {s["id"]: s for s in view.get("styles", []) if isinstance(s, dict) and "id" in s}
    names = {s["id"]: s.get("name") or s["id"] for s in view.get("styles", []) if isinstance(s, dict) and "id" in s}
    sold = {s["id"]: s for s in base.get("style", [])}
    styles = []
    for sid, s in by_style.items():
        styles.append(dict(id=str(sid), name=str(names.get(sid, sid)), units=s["units"],
                           sizes=[dict(size=k, units=s["sizes"][k]) for k in order if k in s["sizes"]]
                           + [dict(size=k, units=v) for k, v in s["sizes"].items() if k not in order],
                           colors=[dict(color=k, units=v) for k, v in sorted(s["colors"].items(), key=lambda x: -x[1])],
                           forecast=(fc.get(sid) or {}).get("f_mean"),
                           expected_sold=round(float((sold.get(sid) or {}).get("sold") or 0)),
                           sellout=(sold.get(sid) or {}).get("sellout")))
    styles.sort(key=lambda x: -x["units"])
    return dict(
        styles=styles[:MAX_STYLES_SHOWN], more_styles=max(0, len(styles) - MAX_STYLES_SHOWN),
        units=int(base["units"]), budget=base.get("budget"), cash_used=base.get("cash_used"),
        expected_leftover=base.get("leftover"), costs_guessed=len(costs),
        stats=(parsed.get("checks") or {}).get("stats", {}).get("orders_summary") or {},
        seconds=round(time.time() - t0, 1),
    )


def encode(obj: dict) -> bytes:
    return json.dumps(obj, default=str).encode()


__all__ = ["plan_file", "encode", "WorkbookError"]
