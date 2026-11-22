"""Real-world export layouts Egyptian brands upload (fixtures made by tests/fixtures/formats/make.py).

Store exports must parse by name rules alone; hand-made Arabic sheets parse once the AI maps the
Arabic headers (simulated here with the mapping the AI returns).
"""
import pathlib

import pytest

from fasla.simple import SimpleError, clean_size, parse_dates, parse_orders, read_table, unknown_statuses

F = pathlib.Path(__file__).parent / "fixtures" / "formats"
AR_MAP = {"date": "التاريخ", "style": "المنتج", "size": "المقاس", "color": "اللون", "qty": "الكمية",
          "price": "السعر", "zone": "المحافظة", "status": "الحالة", "courier": "شركة_الشحن"}


def load(name):
    df, full = read_table((F / name).read_bytes(), name)
    assert not full
    return df


@pytest.mark.parametrize("name", ["A_shopify.csv", "B_woo_aoe.csv", "C_woo_analytics.csv"])
def test_store_exports_parse_without_ai(name):
    r = parse_orders(load(name))
    o = r["orders"]
    assert int(o.qty.sum()) == 492
    assert set(o["size"]) == {"S", "M", "L", "XL"}
    assert o.style_id.nunique() == 3
    assert o.date.min().year == 2025 and o.date.max().year == 2026
    assert (o.status == "refused").sum() > 30


def test_arabic_sheet_with_title_rows_day_first_dates_and_arabic_digits():
    df = load("D_arabic_manual.xlsx")
    with pytest.raises(SimpleError) as e:
        parse_orders(df)
    assert e.value.columns_problem   # the AI mapping step takes over
    assert set(unknown_statuses(df, AR_MAP)) == {"تم التسليم", "مرتجع", "استبدال"}
    r = parse_orders(df, columns=AR_MAP, status_map={"تم التسليم": "delivered", "مرتجع": "refused", "استبدال": "size_exchange"})
    o = r["orders"]
    assert int(o.qty.sum()) == 492              # rows with ٣ and ٣٥٠ ج.م are kept
    assert o.date.min() == parse_dates(__import__("pandas").Series(["01/03/2025"])).iloc[0]
    assert {"refused", "size_exchange"} <= set(o.status)


def test_one_column_per_size_is_unpivoted():
    r = parse_orders(load("E_wide_sizes.xlsx"), columns={"date": "الشهر", "style": "المنتج", "color": "اللون", "price": "السعر"})
    assert set(r["orders"]["size"]) == {"S", "M", "L", "XL"}


def test_courier_export_gets_a_clear_message():
    with pytest.raises(SimpleError) as e:
        parse_orders(load("F_bosta.xlsx"))
    assert "courier export" in e.value.errors[0]


def test_size_words_and_dates():
    assert [clean_size(x) for x in ["لارج", "Medium", "x-large", "مقاس 38", "٤٢", "Free"]] == ["L", "M", "XL", "38", "42", "FREE SIZE"]
    import pandas as pd
    d = parse_dates(pd.Series(["05/11/2025", "13/11/2025"]))
    assert (d.dt.month == 11).all()
    d = parse_dates(pd.Series(["11/05/2025", "11/13/2025"]))   # month-first only when the data proves it
    assert (d.dt.month == 11).all()
