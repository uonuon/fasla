-- Fasla inside Shopify: a shop is a Fasla "user" with a stable UUID derived from its myshopify domain.
-- Shops sign in through Shopify (App Bridge session tokens), not Supabase Auth, so their brands and plans
-- have no matching auth.users row. Drop the foreign keys to auth.users; row level security still limits
-- Supabase-authenticated users to their own rows (user_id = auth.uid()), and shop rows are only reachable
-- through the server, which verifies the Shopify session token.
alter table public.brands drop constraint if exists brands_user_id_fkey;
alter table public.plans  drop constraint if exists plans_user_id_fkey;
