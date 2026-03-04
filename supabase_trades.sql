-- Create trades table to persist trade history
create table if not exists public.trades (
  id uuid not null default gen_random_uuid (),
  user_id uuid not null,
  contract_id text not null,
  symbol text not null,
  signal text not null,
  stake numeric null,
  entry_price numeric null,
  multiplier numeric null,
  entry_source text null,
  exit_price numeric null,
  profit numeric null,
  status text null,
  duration integer null,
  timestamp timestamp with time zone null default now(),
  created_at timestamp with time zone null default now(),
  constraint trades_pkey primary key (id),
  constraint trades_contract_id_key unique (contract_id),
  constraint trades_user_id_fkey foreign KEY (user_id) references auth.users (id)
) TABLESPACE pg_default;

-- Ensure unique constraint exists when table already existed before this script.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'trades_contract_id_key'
      AND conrelid = 'public.trades'::regclass
  ) THEN
    ALTER TABLE public.trades
      ADD CONSTRAINT trades_contract_id_key UNIQUE (contract_id);
  END IF;
END $$;

-- Enable RLS
alter table public.trades enable row level security;

-- Policies

-- DROP existing policies if re-running to avoid conflicts
drop policy if exists "Users can insert their own trades" on public.trades;
drop policy if exists "Users can view their own trades" on public.trades;
drop policy if exists "Admins can view all trades" on public.trades;
drop policy if exists "Users and Admins can view trades" on public.trades;


-- Users can insert their own trades
-- Optimization: Wrap auth.uid() in (select ...) to prevent re-evaluation per row
create policy "Users can insert their own trades"
  on public.trades for insert
  with check ( (select auth.uid()) = user_id );

-- Unified SELECT policy
-- optimizations:
-- 1. Combine User and Admin policies to avoid "Multiple Permissive Policies" warning
-- 2. Use (select ...) for auth functions to optimize query plan
create policy "Users and Admins can view trades"
  on public.trades for select
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );
