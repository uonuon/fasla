# Fasla as a Shopify app

Fasla runs inside the Shopify admin. The merchant clicks **Read orders**, checks costs and factory minimums,
and gets the production plan plus the Excel report. Same engine as the web app.

## What's built

| Part | Where |
|---|---|
| Embedded page (App Bridge) | `fasla/shopify_app.html`, served at `/shopify/` |
| Session tokens, token exchange + refresh, encrypted token storage | `fasla/shopify.py`, `fasla/main.py` |
| Order import (line items only, no customer data) | `fasla/shopify.py` → `ORDERS_QUERY`, `order_rows` |
| Webhooks: `app/uninstalled`, `customers/data_request`, `customers/redact`, `shop/redact` | `POST /webhooks/shopify` |
| App config | `shopify.app.toml` |
| DB change (shops are users without a Supabase login) | `supabase/migrations/0003_shopify_shops.sql` |
| Tests (fake Shopify) | `tests/test_shopify.py` |

The Shopify app stays off until `SHOPIFY_API_KEY` and `SHOPIFY_API_SECRET` are set.

## Steps for Mina (in order)

1. **Create a Shopify Partner account** at partners.shopify.com (free).
2. **Create a development store** from the Partner dashboard (free, for testing).
3. **Create the app** in the Dev Dashboard: name "Fasla". Copy the **Client ID** and **Client secret**.
4. **Run the migration** `supabase/migrations/0003_shopify_shops.sql` in the Supabase SQL editor (one time).
5. **Add environment variables** on the server: `SHOPIFY_API_KEY` (client ID), `SHOPIFY_API_SECRET`,
   optionally `SHOPIFY_TOKEN_KEY` (see `.env.example`). Redeploy.
6. **Put the client ID in `shopify.app.toml`** and run `shopify app deploy` (Shopify CLI) to push the URLs,
   scopes and webhooks.
7. **Protected customer data:** in the Dev Dashboard, request access to **level 1** customer data
   (orders). Fasla does not need names, emails, phones or addresses (level 2).
8. **`read_all_orders`:** Shopify rejects it in the app's scope list until it's approved. Request it in the
   Dev Dashboard; without it Shopify only returns the last 60 days of orders, which is too short for a season plan.
   Done so far (23 Nov): dev store `fasla-test` (Advanced plan, generated test data) and app "Fasla" (version fasla-1).
9. **Install on the development store**, add test orders (or import a CSV of products and orders), open Fasla.
10. **Pricing:** set up plans in the Partner dashboard (Shopify-managed pricing). Suggested: Free (up to
    300 orders a month, one plan per season), Growth $29/month, Pro $79/month.
11. **Listing:** submit for review with the text below, screenshots, and the privacy policy URL
    (fasla.dotcraft.app/privacy).

## Listing draft

**Name:** Fasla: Production Planner

**Tagline (≤ 62 chars):** Know how many of each style, size and colour to produce

**Intro:** Fasla reads your orders and tells you how many pieces of each style, colour and size to produce
next season, within your budget and your factory's minimums.

**Key benefits**
- Quantities per style, colour and size, with the likely demand range for each style
- Corrects for sizes that sold out early, so best sellers aren't under-produced again
- Respects your budget, cash in the bank, factory minimum per style and per colour
- Cash week by week, from paying the factory until sales come back
- Excel report you can send to your workshop

**Privacy line:** Fasla reads order lines only (date, product, size, colour, quantity, price, status). It
never reads customer names, emails, phones or addresses.

## Known gaps before public launch
- No billing gate yet: the free/paid limit isn't enforced (plans read from `activeSubscriptions`).
- No zone/courier advice (needs shipping province, a protected field: add once level access is confirmed).
- Large stores (> 20,000 orders in the window) are cut at 20,000; switch to bulk operations if needed.
- Arabic version of the embedded page.
