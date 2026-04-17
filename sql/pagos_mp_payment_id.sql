-- Idempotencia webhooks Mercado Pago: un mismo payment_id no puede acreditar dos veces.
-- Ejecutar en Supabase → SQL Editor (o migración) antes de producción.

alter table public.pagos add column if not exists mp_payment_id text;

create unique index if not exists pagos_mp_payment_id_key
  on public.pagos (mp_payment_id)
  where mp_payment_id is not null;
