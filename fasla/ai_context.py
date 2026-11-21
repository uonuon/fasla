"""A compact, model-friendly summary of a finished plan (a few thousand tokens at most)."""
from __future__ import annotations


def recommended_index(view: dict) -> int:
    """Same rule as the dashboard: most expected profit that keeps 15% of today's cash as a cushion."""
    vs = view["variants"]
    cushion = max(0.0, 0.15 * float(view["settings"].get("start_cash", 0) or 0))

    def best(ok):
        idx = [i for i, v in enumerate(vs) if ok(v)]
        return max(idx, key=lambda i: vs[i]["profit"]) if idx else -1

    i = best(lambda v: v["cash_min"] >= cushion)
    if i < 0:
        i = best(lambda v: v["cash_min"] >= 0)
    if i < 0:   # every option runs out of cash: the least risky one
        i = max(range(len(vs)), key=lambda k: vs[k]["cash_min"])
    return i


def plan_context(view: dict, brand: str) -> dict:
    s = view["settings"]
    base = view["variants"][view["base"]]
    rec = recommended_index(view)
    meta = {m["id"]: m for m in view["styles"]}
    sizes = view.get("sizes", [])
    # size split of the base plan per style
    split: dict[str, dict[str, int]] = {}
    for k, q in zip(view.get("sku", []), base.get("Q", [])):
        split.setdefault(k["s"], {}).setdefault(k["z"], 0)
        split[k["s"]][k["z"]] += int(q)
    styles = []
    for st in sorted(base["style"], key=lambda x: -x["planned"]):
        m = meta.get(st["id"], {})
        styles.append(dict(
            name=m.get("name", st["id"]), make=st["planned"], likely_demand=[round(m.get("f_p10", 0)), round(m.get("f_p90", 0))],
            chance_sells_out=st["sellout"], expected_left_over=round(st["left"]), cost=m.get("cost"), price=m.get("price"),
            based_on=m.get("source"), owner_estimate=m.get("owner"),
            by_size={z: split.get(st["id"], {}).get(z, 0) for z in sizes if split.get(st["id"], {}).get(z, 0)}))
    variants = [dict(budget_pct=round(v["mult"] * 100), budget=round(v["budget"]), pieces=v["units"],
                     expected_profit=round(v["profit"]), profit_8_in_10=[round(v["p10"]), round(v["p90"])],
                     lowest_cash=round(v["cash_min"]), lowest_cash_week=v["cash_min_week"],
                     left_over_pieces=round(v["leftover"]), demand_met=v["fill"]) for v in view["variants"]]
    c = view["couriers"]
    zones = []
    for z in c["zones"]:
        now = next((a for a in z["alts"] if a["courier"] == z["now"]), None)
        zones.append(dict(zone=z["zone"], orders=z["orders"], courier_now=z["now"], best_courier=z["pick"],
                          delivered_rate=z["rate"], profit_per_order=round(z["profit"]),
                          gain_vs_now=round(z["profit"] - now["profit"]) if now else 0))
    return dict(
        brand=brand, currency="EGP",
        history=view["history"],
        settings={k: s.get(k) for k in ("budget", "start_cash", "factory_moq", "lead_weeks", "deposit_share",
                                         "leftover_share", "remit_weeks", "ads_share", "fixed_weekly")},
        budget_options=variants, budget_entered_pct=100, recommended_budget_pct=round(view["variants"][rec]["mult"] * 100),
        usual_way_profit_at_100pct=round(view["usual"]["profit"]),
        styles=styles,
        couriers=dict(profit_per_order_now=c["now"], profit_per_order_best=c["plan"], orders_per_season=c["orders"], zones=zones),
        refusals=dict(overall=view["refusal"]["overall"], by_zone=view["refusal"]["zones"]),
        exchanges=dict(brand_average=view["exchanges"]["base"],
                       flagged=[dict(style=x["name"], rate=x["rate"], worst_size=x["size"], worst_size_rate=x["size_rate"])
                                for x in view["exchanges"]["rows"] if x["flag"] and x["rate"] > 0]),
        data_notes=view.get("problems", [])[:8],
    )
