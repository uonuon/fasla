"""Read and check an uploaded workbook, then run the planner on it."""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from brandopt.excel import SETTINGS, check_inputs, write_inputs, write_report
from brandopt.forecast import assign_seasons
from brandopt.view import build_view

REQUIRED = {
    "Products": ["style_id", "name", "category", "color", "size", "unit_cost", "price"],
    "Orders": ["date", "style_id", "color", "size", "qty", "zone", "courier", "status"],
    "Production": ["season", "style_id", "color", "size", "produced"],
    "Couriers": ["courier", "zone", "fee", "return_fee"],
    "Settings": ["setting", "value"],
}
NUMERIC = {"Products": ["unit_cost", "price"], "Orders": ["qty"], "Production": ["produced"],
           "Couriers": ["fee", "return_fee"]}
SETTING_KEYS = [k for k, _, _ in SETTINGS]
EDITABLE = {  # key: (min, max, kind)
    "budget": (10_000, 1e9, float), "factory_moq": (0, 100_000, int), "min_per_color": (0, 100_000, int),
    "leftover_share": (0.0, 0.95, float), "start_cash": (-1e9, 1e10, float), "deposit_share": (0.0, 1.0, float),
    "lead_weeks": (0, 52, int), "remit_weeks": (0, 12, int), "ads_share": (0.0, 0.9, float), "fixed_weekly": (0, 1e8, float),
}


class WorkbookError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def read_workbook(data: bytes, max_rows: int = 300_000) -> dict:
    """Parse + validate. Raises WorkbookError with plain-language problems the owner can fix."""
    if not data[:4] == b"PK\x03\x04":
        raise WorkbookError(["This is not an Excel .xlsx file. Download the template, fill it, and upload it again."])
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                raise WorkbookError(["The workbook is too large once unpacked."])
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl")
    except WorkbookError:
        raise
    except Exception:  # noqa: BLE001
        raise WorkbookError(["We couldn't open this workbook. Save it again as .xlsx and retry."])
    sheets = {str(k).strip(): v for k, v in sheets.items()}
    errors, warnings = [], []
    for name, cols in REQUIRED.items():
        if name not in sheets:
            errors.append(f"Sheet '{name}' is missing.")
            continue
        df = _norm_cols(sheets[name]).dropna(how="all")
        miss = [c for c in cols if c not in df.columns]
        if miss:
            errors.append(f"Sheet '{name}' is missing column(s): {', '.join(miss)}.")
        if len(df) > max_rows:
            errors.append(f"Sheet '{name}' has more than {max_rows:,} rows.")
        sheets[name] = df
    if errors:
        raise WorkbookError(errors)
    for name, cols in NUMERIC.items():
        df = sheets[name]
        for c in cols:
            v = pd.to_numeric(df[c], errors="coerce")
            bad = v.isna() & df[c].notna()
            if bad.any():
                errors.append(f"'{name}' column '{c}' has {int(bad.sum())} value(s) that aren't numbers (first at row {int(bad.idxmax()) + 2}).")
            df[c] = v
        sheets[name] = df
    for name in ("Products", "Orders", "Production"):
        for c in ("style_id", "color", "size"):
            sheets[name][c] = sheets[name][c].astype(str).str.strip()
    o = sheets["Orders"]
    o["date"] = pd.to_datetime(o["date"], errors="coerce")
    if o["date"].isna().any():
        errors.append(f"'Orders' has {int(o['date'].isna().sum())} row(s) without a valid date.")
    o["status"] = o["status"].astype(str).str.strip().str.lower()
    for c in ("zone", "courier"):
        o[c] = o[c].astype(str).str.strip()
    if "price" not in o.columns:
        o = o.merge(sheets["Products"][["style_id", "color", "size", "price"]], on=["style_id", "color", "size"], how="left")
    sheets["Orders"] = o
    cr = sheets["Couriers"]
    for c in ("courier", "zone"):
        cr[c] = cr[c].astype(str).str.strip()
    if "remit_days" not in cr.columns:
        cr["remit_days"] = 7
    p = sheets["Products"]
    if p.duplicated(["style_id", "color", "size"]).any():
        errors.append("'Products' lists the same style + colour + size more than once.")
    if len(o) < 1:
        errors.append("'Orders' is empty. Add last season's orders.")
    if len(sheets["Production"]) < 1:
        errors.append("'Production' is empty. Add how many pieces you produced.")
    st = dict(zip(sheets["Settings"]["setting"].astype(str).str.strip(), sheets["Settings"]["value"]))
    settings = {}
    for k in SETTING_KEYS:
        dflt = next(v for kk, v, _ in SETTINGS if kk == k)
        try:
            settings[k] = float(st.get(k, dflt))
        except (TypeError, ValueError):
            errors.append(f"Setting '{k}' must be a number.")
    oe = sheets.get("OwnerEstimates")
    owner = {}
    if oe is not None and len(oe):
        oe = _norm_cols(oe).dropna(how="all")
        if {"style_id", "expected_units"} <= set(oe.columns):
            oe["expected_units"] = pd.to_numeric(oe["expected_units"], errors="coerce")
            owner = {str(k).strip(): float(v) for k, v in zip(oe.style_id, oe.expected_units) if pd.notna(v) and v > 0}
    if errors:
        raise WorkbookError(errors)
    try:
        so, sp, labels = assign_seasons(o, sheets["Production"])
    except ValueError as e:
        raise WorkbookError([str(e)])
    if len(so) < len(o):
        warnings.append(f"{len(o) - len(so)} orders fall outside the seasons in the Production sheet and were ignored.")
    warnings += check_inputs(p, o, sheets["Production"], cr)
    if len(so) < 300:
        warnings.append("Fewer than 300 orders: the plan will be rough. More history makes it sharper.")
    if not owner:
        warnings.append("No owner estimates: adding your own guess per style improves the plan, especially for new styles.")
    new_styles = sorted(set(p.style_id) - set(sheets["Production"].style_id))
    stats = dict(orders=int(len(so)), products=int(len(p)), styles=int(p.style_id.nunique()), seasons=labels,
                 couriers=sorted(cr.courier.unique().tolist()), zones=int(o.zone.nunique()),
                 date_from=str(o.date.min().date()), date_to=str(o.date.max().date()), new_styles=new_styles,
                 owner_estimates=len(owner))
    inp = dict(settings=settings, products=p, orders=o, production=sheets["Production"], couriers=cr, owner=owner,
               problems=warnings)
    return dict(inp=inp, checks=dict(errors=[], warnings=warnings, stats=stats), settings=settings)


def clean_settings(user: dict, base: dict) -> dict:
    out = dict(base)
    for k, v in (user or {}).items():
        if k not in EDITABLE:
            continue
        lo, hi, kind = EDITABLE[k]
        try:
            x = kind(float(v))
        except (TypeError, ValueError):
            raise WorkbookError([f"'{k}' must be a number."])
        if not lo <= x <= hi:
            raise WorkbookError([f"'{k}' must be between {lo:g} and {hi:g}."])
        out[k] = x
    return out


def run_plan_on(data: bytes, settings: dict, brand_name: str, solver_seconds: int = 25):
    parsed = read_workbook(data)
    inp = parsed["inp"]
    inp["settings"] = clean_settings(settings, parsed["settings"])
    view, res = build_view(inp, 200, (0.7, 0.85, 1.0, 1.15, 1.3), solver_seconds, brand_name)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "report.xlsx"
        write_report(res, str(path))
        report = path.read_bytes()
    base = view["variants"][view["base"]]
    summary = dict(profit=base["profit"], units=base["units"], cash_used=base["cash_used"], budget=base["budget"],
                   leftover=base["leftover"], cash_min=base["cash_min"])
    return view, report, summary


def template_bytes(example: bool) -> bytes:
    """Empty template (headers + one sample row per sheet) or the filled example brand."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.xlsx"
        if example:
            from brandopt.sim import BrandWorld, default_styles
            from brandopt.backtest import owner_plans
            w = BrandWorld(default_styles(), seed=7)
            h = w.simulate_history()
            own = owner_plans(w, h, h["products"], 2, np.random.default_rng(8))
            gut = pd.Series(own["plans"]["Owner A: gut feel, usual size split"], index=h["products"].style_id).groupby(level=0).sum()
            oe = pd.DataFrame({"style_id": gut.index, "expected_units": gut.values})
            write_inputs(str(path), h["products"], h["orders"], h["production"], h["couriers"], oe,
                         settings={"budget": round(own["budget"], -3)})
        else:
            products = pd.DataFrame([dict(style_id="TEE01", name="Oversized Tee", category="tee", color="Black", size=s,
                                          unit_cost=230, price=550) for s in ["S", "M", "L", "XL"]])
            orders = pd.DataFrame([dict(order_id="1001", date="2025-10-03", style_id="TEE01", color="Black", size="M", qty=1,
                                        price=550, zone="Cairo", courier="Bosta", status="delivered", shipping_fee=75, return_fee=0)])
            production = pd.DataFrame([dict(season="Winter 2025/26", season_start="2025-10-01", style_id="TEE01", color="Black",
                                            size=s, produced=q) for s, q in zip(["S", "M", "L", "XL"], [30, 60, 60, 40])])
            couriers = pd.DataFrame([dict(courier="Bosta", zone="Cairo", fee=75, return_fee=38, remit_days=5)])
            oe = pd.DataFrame([dict(style_id="TEE01", expected_units=250)])
            write_inputs(str(path), products, orders, production, couriers, oe)
        return path.read_bytes()
