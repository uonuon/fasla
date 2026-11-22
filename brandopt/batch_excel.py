"""Excel in, Excel out for batch brands (food in jars, oils, creams): one input workbook, one report."""
from __future__ import annotations

import math
import sys

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from .batch import BatchSettings, materials_plan, plan_batch
from .excel import _style_sheet

SETTINGS = [
    ("plan_start", "", "Day you make the next batch (YYYY-MM-DD). Empty = a week after your last order",
     "يوم الإنتاج الجاي. سيبه فاضي = بعد آخر أوردر بأسبوع"),
    ("budget", "", "Cash for this batch in EGP. Empty = no limit", "الفلوس المتاحة للإنتاج. فاضي = مفيش حد"),
    ("review_months", 1, "How often you produce, in months, unless a product says otherwise",
     "بتنتج كل قد إيه (بالشهور)"),
    ("months_ahead", 4, "Months to forecast", "عدد الشهور اللي نتوقعها"),
    ("holding_cost_month", 0.03, "Cost of keeping one unit in stock one more month, as a share of its cost",
     "تكلفة تخزين القطعة شهر زيادة كنسبة من تكلفتها"),
    ("max_service", 0.95, "Highest chance of not running out we aim for", "أعلى نسبة إننا مانخلّصش"),
]

README = [
    ("Fasla batch planner: how much of each product to make next", "فاصلة: تعمل كام من كل منتج الدفعة الجاية"),
    ("", ""),
    ("1. Products: one row per product x variant (flavour/scent) x pack size.",
     "١. المنتجات: سطر لكل منتج × نوع (طعم/ريحة) × حجم العبوة."),
    ("   price, unit_cost, on_hand (in stock now). Optional: batch_min_amount (ml or g per batch),",
     "   السعر، التكلفة، المخزون الحالي. اختياري: أقل دفعة (مل أو جرام)،"),
    ("   batch_min_units, batch_multiple, shelf_life_months, lead_time_days (days until ready to sell),",
     "   أقل عدد، مضاعفات، الصلاحية بالشهور، أيام لحد ما يبقى جاهز للبيع،"),
    ("   make_every_days (7 = weekly), expected_monthly_units (only for new products).",
     "   بتنتجه كل كام يوم، والمتوقع شهرياً للمنتج الجديد بس."),
    ("2. Orders: every order line: date, product, variant, pack, qty, status.",
     "٢. الأوردرات: كل سطر: التاريخ، المنتج، النوع، الحجم، الكمية، الحالة."),
    ("   Cancelled orders are ignored; refused ones still count as demand.",
     "   الملغي مش بيتحسب، والمرتجع بيتحسب طلب."),
    ("3. BOM and Materials (optional): jars, lids, labels, honey, garlic per unit,",
     "٣. الخامات (اختياري): برطمانات، غطيان، ليبلز، عسل، توم لكل وحدة،"),
    ("   and how long each takes to arrive. You get what to order and the last day to order it.",
     "   ووقت وصول كل حاجة. هتعرف تطلب إيه وآخر يوم تطلبه."),
    ("Grey rows are examples: replace them with your own data.", "السطور الرمادي أمثلة: امسحها وحط بياناتك."),
]

PRODUCT_COLS = ["product", "variant", "pack", "price", "unit_cost", "on_hand", "batch_min_amount", "batch_min_units",
                "batch_multiple", "shelf_life_months", "lead_time_days", "make_every_days", "expected_monthly_units"]
ORDER_COLS = ["date", "order_id", "product", "variant", "pack", "qty", "status"]
GREY = PatternFill("solid", fgColor="E7E7E7")


def write_template(path: str, example: bool = True) -> None:
    from .batch_sample import sample_food_brand, sample_materials
    if example:
        orders, products = sample_food_brand()
        bom, mats = _food_materials()
        orders = orders[ORDER_COLS]
        products = products.reindex(columns=PRODUCT_COLS).fillna(0)
    else:
        orders = pd.DataFrame(columns=ORDER_COLS)
        products = pd.DataFrame(columns=PRODUCT_COLS)
        bom = pd.DataFrame(columns=["product", "variant", "pack", "material", "qty_per_unit"])
        mats = pd.DataFrame(columns=["material", "on_hand", "lead_time_days", "order_multiple", "unit_cost"])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(README, columns=["Read me", "اقرأني"]).to_excel(xw, sheet_name="README", index=False)
        pd.DataFrame([(k, v, e, a) for k, v, e, a in SETTINGS],
                     columns=["setting", "value", "meaning", "المعنى"]).to_excel(xw, sheet_name="Settings", index=False)
        products.to_excel(xw, sheet_name="Products", index=False)
        orders.to_excel(xw, sheet_name="Orders", index=False)
        bom.to_excel(xw, sheet_name="BOM", index=False)
        mats.to_excel(xw, sheet_name="Materials", index=False)
    wb = load_workbook(path)
    for ws in wb.worksheets:
        _style_sheet(ws, pd.DataFrame(columns=[c.value for c in ws[1]]),
                     widths={"meaning": 70, "المعنى": 50, "Read me": 95, "اقرأني": 70, "product": 24, "material": 24})
        if example and ws.title in ("Products", "Orders", "BOM", "Materials"):
            for row in ws.iter_rows(min_row=2):
                for c in row:
                    c.fill = GREY
    wb.save(path)


def _food_materials():
    bom = pd.DataFrame([
        ("Bone Broth", "*", "500 ml", "Jar 500 ml", 1), ("Bone Broth", "*", "1 L", "Jar 1 L", 1),
        ("Bone Broth", "Beef", "500 ml", "Beef bones (kg)", 0.6), ("Bone Broth", "Beef", "1 L", "Beef bones (kg)", 1.2),
        ("Bone Broth", "Chicken", "500 ml", "Chicken bones (kg)", 0.5),
        ("Fermented Garlic Honey", "*", "250 g", "Jar 250 g", 1), ("Fermented Garlic Honey", "*", "500 g", "Jar 500 g", 1),
        ("Fermented Garlic Honey", "*", "250 g", "Raw honey (kg)", 0.2), ("Fermented Garlic Honey", "*", "500 g", "Raw honey (kg)", 0.4),
        ("Fermented Garlic Honey", "*", "250 g", "Garlic (kg)", 0.06), ("Fermented Garlic Honey", "*", "500 g", "Garlic (kg)", 0.12),
        ("Beef Tallow", "*", "200 g", "Jar 200 g", 1), ("Figs & Olive", "*", "400 g", "Jar 400 g", 1),
        ("*", "*", "*", "Label", 1),
    ], columns=["product", "variant", "pack", "material", "qty_per_unit"])
    mats = pd.DataFrame([
        ("Jar 500 ml", 40, 10, 24, 14), ("Jar 1 L", 20, 10, 12, 20), ("Jar 250 g", 60, 10, 24, 9),
        ("Jar 500 g", 30, 10, 24, 13), ("Jar 200 g", 50, 10, 24, 8), ("Jar 400 g", 30, 10, 24, 11),
        ("Raw honey (kg)", 10, 7, 5, 220), ("Garlic (kg)", 2, 2, 1, 60),
        ("Beef bones (kg)", 0, 2, 5, 90), ("Chicken bones (kg)", 0, 2, 5, 40), ("Label", 300, 7, 500, 1.2),
    ], columns=["material", "on_hand", "lead_time_days", "order_multiple", "unit_cost"])
    return bom, mats


def read_inputs(path_or_bytes) -> dict:
    xl = pd.read_excel(path_or_bytes, sheet_name=None)
    low = {k.strip().lower(): v for k, v in xl.items()}

    def sheet(*names):
        for n in names:
            if n in low:
                return low[n]
        return None

    products, orders = sheet("products", "المنتجات"), sheet("orders", "الأوردرات", "الطلبات")
    if products is None or orders is None:
        raise ValueError("The workbook needs a Products sheet and an Orders sheet.")
    st = sheet("settings")
    settings = BatchSettings()
    if st is not None and {"setting", "value"} <= set(st.columns):
        for _, r in st.iterrows():
            k, v = str(r.setting).strip(), r.value
            if pd.isna(v) or str(v).strip() == "":
                continue
            if k == "plan_start":
                settings.plan_start = pd.to_datetime(v).date()
            elif k == "months_ahead":
                settings.months_ahead = int(v)
            elif hasattr(settings, k):
                setattr(settings, k, float(v))
    return dict(products=products, orders=orders, bom=sheet("bom"), materials=sheet("materials"), settings=settings)


def run(inp: dict) -> dict:
    res = plan_batch(inp["orders"], inp["products"], inp["settings"])
    bom, mats = inp.get("bom"), inp.get("materials")
    if bom is not None and len(bom) and mats is not None:
        res["materials"] = materials_plan(res, bom, mats)
    else:
        res["materials"] = pd.DataFrame()
    return res


def write_report(res: dict, path: str) -> None:
    plan = res["plan"].copy()
    plan["chance_no_stockout"] = plan.chance_no_stockout.round(3)
    plan["months_of_stock"] = plan.months_of_stock.round(1)
    plan["next_30_days"] = plan.next_30_days.round(0)
    summary = pd.DataFrame([
        ("Batch day", str(res["start"])),
        ("Units to make", int(plan.make.sum())),
        ("Production cost (EGP)", round(res["total_cost"])),
        ("Products to make", int((plan.make > 0).sum())),
    ] + [("Note", n) for n in res["notes"]], columns=["", "value"])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        plan.to_excel(xw, sheet_name="Make this batch", index=False)
        res["batches"].to_excel(xw, sheet_name="Batches", index=False)
        if len(res.get("materials", [])):
            res["materials"].to_excel(xw, sheet_name="Materials to order", index=False)
        res["forecast"].round(0).to_excel(xw, sheet_name="Forecast by month", index=False)
    wb = load_workbook(path)
    for ws in wb.worksheets:
        _style_sheet(ws, pd.DataFrame(columns=[c.value for c in ws[1]]),
                     widths={"": 24, "value": 90, "product": 24, "note": 60, "material": 24},
                     money=("cost", "value"), pct=("chance_no_stockout",))
    ws = wb["Make this batch"]
    for row in ws.iter_rows(min_row=2):
        if row[4].value and row[4].value > 0:
            row[4].font = Font(bold=True)
    wb.save(path)


def main(argv=None):
    a = argv or sys.argv[1:]
    if len(a) >= 2 and a[0] == "template":
        write_template(a[1], example=len(a) < 3 or a[2] != "blank")
        print("wrote", a[1])
    elif len(a) == 3 and a[0] == "plan":
        res = run(read_inputs(a[1]))
        write_report(res, a[2])
        print(res["plan"][["product", "variant", "pack", "on_hand", "make"]].to_string(index=False))
        print("cost EGP", f"{res['total_cost']:,.0f}")
    else:
        print("usage: python -m brandopt.batch_excel template out.xlsx [blank] | plan input.xlsx report.xlsx")


if __name__ == "__main__":
    main()
