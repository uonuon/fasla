"""Synthetic Egyptian clothing brand ("Nile Thread", fictional) for testing.

Everything here is MADE UP but tuned to public Egyptian ranges:
  * courier fees EGP 55-150 by zone (published 2026 rate cards)
  * COD return-to-origin 25-35% on average
  * factory minimum order ~100 pieces per design
The generator keeps the TRUE demand parameters so a backtest can score any
production plan against demand the planner never saw.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SIZES = ["S", "M", "L", "XL", "XXL"]

# true size curves by category (share of units)
SIZE_CURVE = {
    "tee":     [0.12, 0.30, 0.30, 0.18, 0.10],
    "hoodie":  [0.09, 0.26, 0.32, 0.21, 0.12],
    "pants":   [0.13, 0.32, 0.29, 0.17, 0.09],
    "jacket":  [0.08, 0.25, 0.33, 0.22, 0.12],
}
# what a typical owner assumes when splitting a run across sizes
OWNER_SIZE_SPLIT = [0.15, 0.25, 0.25, 0.20, 0.15]

ZONES = ["Cairo", "Giza", "Alexandria", "Delta", "Canal", "Upper Egypt", "Red Sea"]
ZONE_SHARE = [0.38, 0.20, 0.11, 0.15, 0.04, 0.10, 0.02]

# fictional couriers; fee (EGP), true delivery-success probability by zone
COURIERS = {
    "FastBox":     {"fee": [75, 75, 95, 95, 95, 110, 150],
                    "p":   [0.78, 0.77, 0.74, 0.68, 0.70, 0.58, 0.62],
                    "remit_days": 5},
    "NileExpress": {"fee": [65, 65, 85, 85, 85, 95, 140],
                    "p":   [0.71, 0.70, 0.70, 0.69, 0.68, 0.67, 0.64],
                    "remit_days": 7},
    "CityRunner":  {"fee": [55, 60, None, None, None, None, None],   # Greater Cairo only
                    "p":   [0.83, 0.81, None, None, None, None, None],
                    "remit_days": 3},
}
RETURN_FEE_SHARE = 0.5     # refused parcel: courier charges half the fee to bring it back


@dataclass
class Style:
    style_id: str
    name: str
    category: str
    unit_cost: float
    price: float
    colors: list
    color_share: list
    base_season_units: float      # true expected delivered units per season (year 0)
    size_tweak: list = field(default_factory=lambda: [0, 0, 0, 0, 0])
    exchange_rate: float = 0.06   # delivered items sent back for size/fit exchange
    new: bool = False             # no sales history


def default_styles() -> list[Style]:
    S = Style
    return [
        S("NT01", "Classic Oversized Tee", "tee", 230, 550, ["Black", "White", "Olive"], [0.45, 0.35, 0.20], 900),
        S("NT02", "Graphic Tee 'Cairo'", "tee", 250, 600, ["Black", "Sand"], [0.6, 0.4], 520),
        S("NT03", "Heavy Hoodie", "hoodie", 520, 1250, ["Black", "Grey", "Navy", "Brown"], [0.38, 0.27, 0.2, 0.15], 780),
        S("NT04", "Zip Hoodie", "hoodie", 560, 1350, ["Black", "Grey"], [0.55, 0.45], 380),
        S("NT05", "Quarter-Zip Fleece", "hoodie", 480, 1150, ["Cream", "Black", "Green"], [0.4, 0.35, 0.25], 300,
          size_tweak=[0.02, 0.03, 0.0, -0.03, -0.02]),
        S("NT06", "Cargo Pants", "pants", 420, 980, ["Black", "Khaki", "Olive"], [0.42, 0.33, 0.25], 560),
        S("NT07", "Slim Jogger", "pants", 360, 850, ["Black", "Grey"], [0.6, 0.4], 420,
          size_tweak=[-0.03, -0.02, 0.0, 0.03, 0.02], exchange_rate=0.17),   # runs small
        S("NT08", "Wide-Leg Sweatpants", "pants", 380, 900, ["Grey", "Black", "Beige"], [0.4, 0.35, 0.25], 470),
        S("NT09", "Puffer Jacket", "jacket", 950, 2200, ["Black", "Olive"], [0.65, 0.35], 210),
        S("NT10", "Denim Jacket", "jacket", 780, 1800, ["Blue", "Black"], [0.55, 0.45], 160),
        S("NT11", "Knit Sweater", "hoodie", 540, 1300, ["Cream", "Brown", "Black"], [0.4, 0.3, 0.3], 260, new=True),
        S("NT12", "Varsity Jacket", "jacket", 880, 2000, ["Black/White", "Green/White"], [0.6, 0.4], 150, new=True),
    ]


@dataclass
class BrandWorld:
    styles: list
    weeks: int = 16                 # winter season length
    growth: float = 1.15            # yearly growth of the brand
    season_cv: float = 0.28         # uncertainty of a style's season total
    deliver_rate: float = 0.72      # share of COD orders actually delivered with the owner's usual couriers
    seed: int = 7

    # ---------- true demand ----------
    def true_size_shares(self, st: Style) -> np.ndarray:
        s = np.array(SIZE_CURVE[st.category]) + np.array(st.size_tweak)
        s = np.clip(s, 0.01, None)
        return s / s.sum()

    def week_profile(self) -> np.ndarray:
        t = np.arange(self.weeks)
        w = np.exp(-t / 7.0) + 0.35
        w[5] *= 1.6     # Black Friday week
        w[9] *= 1.25    # mid-season drop / holidays
        return w / w.sum()

    def expected_sku_demand(self, year: int, delivered: bool = True) -> pd.DataFrame:
        """base_season_units are ORDERS placed; delivered demand = orders x delivery rate."""
        rows = []
        for st in self.styles:
            if st.new and year < 2:
                continue
            tot = st.base_season_units * self.growth ** year * (self.deliver_rate if delivered else 1.0)
            ss = self.true_size_shares(st)
            for c, cs in zip(st.colors, st.color_share):
                for z, sz in zip(SIZES, ss):
                    rows.append(dict(style_id=st.style_id, color=c, size=z, mean=tot * cs * sz))
        return pd.DataFrame(rows)

    def sample_season_demand(self, year: int, n: int, rng, delivered: bool = True) -> np.ndarray:
        """n scenarios x SKUs of TRUE demand for a season (order of expected_sku_demand)."""
        base = self.expected_sku_demand(year, delivered)
        sid = base["style_id"].to_numpy()
        out = np.zeros((n, len(base)))
        sig = np.sqrt(np.log(1 + self.season_cv ** 2))
        for st in self.styles:
            m = sid == st.style_id
            if not m.any():
                continue
            shock = rng.lognormal(-sig ** 2 / 2, sig, size=n)          # style-level surprise
            mix = rng.dirichlet(base.loc[m, "mean"].to_numpy() / base.loc[m, "mean"].sum() * 300, size=n)
            lam = shock[:, None] * base.loc[m, "mean"].sum() * mix
            out[:, m] = rng.poisson(lam)
        return out

    # ---------- history the owner actually saw ----------
    def owner_production(self, year: int, rng) -> pd.DataFrame:
        """What the owner produced: a gut-feel total per style, split with the owner's habit."""
        rows = []
        for st in self.styles:
            if st.new and year < 2:
                continue
            tot = st.base_season_units * self.growth ** year * self.deliver_rate * rng.uniform(0.75, 1.25)
            tot = max(100, round(tot / 10) * 10)
            for c, cs in zip(st.colors, st.color_share):
                # owners usually split colors close to evenly, a bit toward black
                col_share = (cs + 1 / len(st.colors)) / 2
                for z, sz in zip(SIZES, OWNER_SIZE_SPLIT):
                    rows.append(dict(style_id=st.style_id, color=c, size=z, produced=round(tot * col_share * sz)))
        return pd.DataFrame(rows)

    def simulate_history(self, years=(0, 1)) -> dict:
        rng = np.random.default_rng(self.seed)
        prof = self.week_profile()
        st_by = {s.style_id: s for s in self.styles}
        orders, prod_all = [], []
        oid = 0
        start = {0: pd.Timestamp("2024-10-01"), 1: pd.Timestamp("2025-10-01")}
        cour_names = list(COURIERS)
        for y in years:
            prod = self.owner_production(y, rng)
            prod["season"] = f"Winter {2024 + y}/{25 + y}"
            prod_all.append(prod)
            dem = self.sample_season_demand(y, 1, rng, delivered=False)[0]   # orders placed
            base = self.expected_sku_demand(y, delivered=False)
            stock = prod.set_index(["style_id", "color", "size"])["produced"].to_dict()
            for i, r in base.iterrows():
                key = (r.style_id, r.color, r["size"])
                st = st_by[r.style_id]
                weekly = rng.multinomial(int(dem[i]), prof)
                for w, q in enumerate(weekly):
                    for _ in range(q):
                        if stock[key] <= 0:
                            break      # sold out: this demand is never recorded (censored)
                        zi = rng.choice(len(ZONES), p=ZONE_SHARE)
                        # owner's courier habit: FastBox mostly, some tests of others
                        if zi <= 1 and rng.random() < 0.15:
                            ci = 2
                        else:
                            ci = 0 if rng.random() < 0.72 else 1
                        cn = cour_names[ci]
                        p = COURIERS[cn]["p"][zi]
                        # pricier items get refused a little more
                        p_adj = np.clip(p - 0.00004 * (st.price - 800), 0.3, 0.97)
                        delivered = rng.random() < p_adj
                        exchanged = delivered and rng.random() < st.exchange_rate
                        fee = COURIERS[cn]["fee"][zi]
                        date = start[y] + pd.Timedelta(days=int(7 * w + rng.integers(0, 7)))
                        status = "delivered" if delivered else "refused"
                        if exchanged:
                            status = "size_exchange"
                        orders.append(dict(order_id=f"O{oid:06d}", date=date, style_id=r.style_id, color=r.color,
                                           size=r["size"], qty=1, price=st.price, zone=ZONES[zi], courier=cn,
                                           status=status, shipping_fee=fee,
                                           return_fee=0 if delivered else round(fee * RETURN_FEE_SHARE)))
                        oid += 1
                        if delivered:
                            stock[key] -= 1   # an exchange still sells one piece (another size goes out)
                        # refused parcels come back to stock
        orders = pd.DataFrame(orders).sort_values("date").reset_index(drop=True)
        production = pd.concat(prod_all, ignore_index=True)
        products = []
        for st in self.styles:
            for c in st.colors:
                for z in SIZES:
                    products.append(dict(style_id=st.style_id, name=st.name, category=st.category, color=c, size=z,
                                         unit_cost=st.unit_cost, price=st.price, new_style=st.new))
        couriers = []
        for cn, d in COURIERS.items():
            for zi, zn in enumerate(ZONES):
                if d["fee"][zi] is None:
                    continue
                couriers.append(dict(courier=cn, zone=zn, fee=d["fee"][zi],
                                     return_fee=round(d["fee"][zi] * RETURN_FEE_SHARE), remit_days=d["remit_days"]))
        return dict(orders=orders, production=production, products=pd.DataFrame(products),
                    couriers=pd.DataFrame(couriers))
