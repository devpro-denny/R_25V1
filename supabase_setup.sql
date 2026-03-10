-- 1. Create profiles table if it doesn't exist
create table if not exists public.profiles (
  id uuid references auth.users not null primary key,
  email text,
  role text default 'user',
  is_approved boolean default false,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 2. Add deriv_api_key, stake_amount, and active_strategy columns if they don't exist
do $$
begin
  if not exists (select 1 from information_schema.columns where table_name = 'profiles' and column_name = 'deriv_api_key') then
    alter table public.profiles add column deriv_api_key text;
  end if;

  if not exists (select 1 from information_schema.columns where table_name = 'profiles' and column_name = 'stake_amount') then
    alter table public.profiles add column stake_amount numeric default 50.0;
  end if;

  if not exists (select 1 from information_schema.columns where table_name = 'profiles' and column_name = 'active_strategy') then
    alter table public.profiles add column active_strategy text default 'Conservative';
  end if;

  if not exists (select 1 from information_schema.columns where table_name = 'profiles' and column_name = 'auto_execute_signals') then
    alter table public.profiles add column auto_execute_signals boolean default false;
  end if;
end $$;

-- 3. Helper Function: is_admin (Safe & Cached)
create or replace function public.is_admin()
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  return exists (
    select 1 from public.profiles
    where id = (select auth.uid()) and role = 'admin'
  );
end;
$$;

-- Enable Row Level Security (RLS)
alter table public.profiles enable row level security;

-- 4. CLEANUP: Drop ALL existing/conflicting policies to fix "Multiple Permissive Policies"
drop policy if exists "Public profiles are viewable by everyone" on public.profiles;
drop policy if exists "Users can insert their own profile" on public.profiles;
drop policy if exists "Users can update own profile" on public.profiles;
drop policy if exists "Admins can delete profiles" on public.profiles;
drop policy if exists "Profiles visible to owner and admins" on public.profiles;
drop policy if exists "Admins or owners can update profile" on public.profiles;
drop policy if exists "Admins can update any profile" on public.profiles;

-- 5. CREATE NEW OPTIMIZED POLICIES
-- Use (select auth.uid()) for better performance (fixes auth_rls_initplan warning)

-- SELECT: Users see themselves, Admins see everyone
create policy "Profiles visible to owner and admins"
  on public.profiles for select
  using (
    (select auth.uid()) = id
    or
    (select public.is_admin())
  );

-- INSERT: Users can insert their own profile
create policy "Users can insert their own profile"
  on public.profiles for insert
  with check ( (select auth.uid()) = id );

-- UPDATE: Users update themselves, Admins update anyone
create policy "Admins or owners can update profile"
  on public.profiles for update
  using (
    (select auth.uid()) = id
    or
    (select public.is_admin())
  );

-- DELETE: Only Admins can delete
create policy "Admins can delete profiles"
  on public.profiles for delete
  using ( (select public.is_admin()) );


-- 6. Trigger for New Users
create or replace function public.handle_new_user()
returns trigger 
language plpgsql 
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, role, is_approved)
  values (new.id, new.email, 'user', false)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- ==================== SCALPING BOT MIGRATIONS ====================
-- Phase 6: Database schema updates for scalping bot support

-- 1. Add strategy_type to trades table
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'trades' AND column_name = 'strategy_type'
  ) THEN
    ALTER TABLE public.trades 
    ADD COLUMN strategy_type TEXT NOT NULL DEFAULT 'Conservative';
    
    -- Add check constraint
    ALTER TABLE public.trades
    ADD CONSTRAINT trades_strategy_type_check 
    CHECK (strategy_type IN ('Conservative', 'Scalping', 'RiseFall'));
    
    RAISE NOTICE 'Added strategy_type column to trades table';
  END IF;
END $$;

-- 1b. Add entry_source marker to trades table (system/manual-import origin)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'trades' AND column_name = 'entry_source'
  ) THEN
    ALTER TABLE public.trades
    ADD COLUMN entry_source TEXT NULL;

    RAISE NOTICE 'Added entry_source column to trades table';
  END IF;
END $$;

-- 1c. Add multiplier value snapshot to trades table
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'trades' AND column_name = 'multiplier'
  ) THEN
    ALTER TABLE public.trades
    ADD COLUMN multiplier NUMERIC NULL;

    RAISE NOTICE 'Added multiplier column to trades table';
  END IF;
END $$;

-- 1d. Persist per-trade exit-control toggles so refresh/recovery keeps runtime state.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'trades' AND column_name = 'trailing_enabled'
  ) THEN
    ALTER TABLE public.trades
    ADD COLUMN trailing_enabled BOOLEAN NULL DEFAULT TRUE;

    RAISE NOTICE 'Added trailing_enabled column to trades table';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'trades' AND column_name = 'stagnation_enabled'
  ) THEN
    ALTER TABLE public.trades
    ADD COLUMN stagnation_enabled BOOLEAN NULL DEFAULT TRUE;

    RAISE NOTICE 'Added stagnation_enabled column to trades table';
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'trades' AND column_name = 'entry_source'
  ) THEN
    ALTER TABLE public.trades
    ALTER COLUMN entry_source SET DEFAULT 'system';
  END IF;
END $$;

UPDATE public.trades
SET entry_source = 'system'
WHERE entry_source IS NULL;

UPDATE public.trades
SET trailing_enabled = TRUE
WHERE trailing_enabled IS NULL;

UPDATE public.trades
SET stagnation_enabled = TRUE
WHERE stagnation_enabled IS NULL;

UPDATE public.trades
SET multiplier = CASE symbol
  WHEN 'R_25' THEN 160
  WHEN 'R_50' THEN 80
  WHEN 'R_75' THEN 50
  WHEN 'R_100' THEN 40
  WHEN '1HZ25V' THEN 160
  WHEN '1HZ50V' THEN 80
  WHEN '1HZ75V' THEN 50
  WHEN '1HZ90V' THEN 45
  WHEN 'stpRNG5' THEN 100
  WHEN 'stpRNG4' THEN 200
  ELSE multiplier
END
WHERE multiplier IS NULL;

-- 6. Scalping runtime state persistence
create table if not exists public.scalping_runtime_state (
  user_id uuid primary key references auth.users (id) on delete cascade,
  loss_cooldown_until timestamp with time zone null,
  daily_trade_count integer not null default 0,
  daily_trade_count_date date null,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_name = 'scalping_runtime_state' and column_name = 'daily_trade_count'
  ) then
    alter table public.scalping_runtime_state
      add column daily_trade_count integer not null default 0;
  end if;

  if not exists (
    select 1 from information_schema.columns
    where table_name = 'scalping_runtime_state' and column_name = 'daily_trade_count_date'
  ) then
    alter table public.scalping_runtime_state
      add column daily_trade_count_date date null;
  end if;
end $$;

create or replace function public.touch_scalping_runtime_state_updated_at()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$;

drop trigger if exists scalping_runtime_state_updated_at on public.scalping_runtime_state;
create trigger scalping_runtime_state_updated_at
  before update on public.scalping_runtime_state
  for each row execute procedure public.touch_scalping_runtime_state_updated_at();

alter table public.scalping_runtime_state enable row level security;

drop policy if exists "Scalping runtime state visible to owner and admins" on public.scalping_runtime_state;
drop policy if exists "Scalping runtime state insert owner or admin" on public.scalping_runtime_state;
drop policy if exists "Scalping runtime state update owner or admin" on public.scalping_runtime_state;
drop policy if exists "Scalping runtime state delete owner or admin" on public.scalping_runtime_state;

create policy "Scalping runtime state visible to owner and admins"
  on public.scalping_runtime_state for select
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

create policy "Scalping runtime state insert owner or admin"
  on public.scalping_runtime_state for insert
  with check (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

create policy "Scalping runtime state update owner or admin"
  on public.scalping_runtime_state for update
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

create policy "Scalping runtime state delete owner or admin"
  on public.scalping_runtime_state for delete
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

-- 7. Rise/Fall cross-process bot session lock table
create table if not exists public.rf_bot_sessions (
  user_id uuid primary key references auth.users (id) on delete cascade,
  started_at timestamp with time zone not null default timezone('utc'::text, now()),
  process_id bigint,
  created_at timestamp with time zone not null default timezone('utc'::text, now()),
  updated_at timestamp with time zone not null default timezone('utc'::text, now())
);

create or replace function public.touch_rf_bot_sessions_updated_at()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$;

drop trigger if exists rf_bot_sessions_updated_at on public.rf_bot_sessions;
create trigger rf_bot_sessions_updated_at
  before update on public.rf_bot_sessions
  for each row execute procedure public.touch_rf_bot_sessions_updated_at();

alter table public.rf_bot_sessions enable row level security;

drop policy if exists "RF bot sessions visible to owner and admins" on public.rf_bot_sessions;
drop policy if exists "RF bot sessions insert owner or admin" on public.rf_bot_sessions;
drop policy if exists "RF bot sessions update owner or admin" on public.rf_bot_sessions;
drop policy if exists "RF bot sessions delete owner or admin" on public.rf_bot_sessions;

create policy "RF bot sessions visible to owner and admins"
  on public.rf_bot_sessions for select
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

create policy "RF bot sessions insert owner or admin"
  on public.rf_bot_sessions for insert
  with check (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

create policy "RF bot sessions update owner or admin"
  on public.rf_bot_sessions for update
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

create policy "RF bot sessions delete owner or admin"
  on public.rf_bot_sessions for delete
  using (
    (select auth.uid()) = user_id
    or
    (select public.is_admin())
  );

-- 2. Backfill existing trades
UPDATE public.trades 
SET strategy_type = 'Conservative' 
WHERE strategy_type IS NULL;

-- 3. Add check constraint to profiles.active_strategy
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.constraint_column_usage
    WHERE table_name = 'profiles' AND constraint_name = 'profiles_active_strategy_check'
  ) THEN
    ALTER TABLE public.profiles
   ADD CONSTRAINT profiles_active_strategy_check
    CHECK (active_strategy IN ('Conservative', 'Scalping', 'RiseFall'));
    
    RAISE NOTICE 'Added check constraint to profiles.active_strategy';
  END IF;
END $$;

-- 4. Backfill profiles with invalid strategy values
UPDATE public.profiles 
SET active_strategy = 'Conservative' 
WHERE active_strategy NOT IN ('Conservative', 'Scalping', 'RiseFall') 
   OR active_strategy IS NULL;

-- 5. Enforce encrypted Deriv API key format for new/updated rows.
-- Existing plaintext rows are tolerated until they are updated.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'profiles_deriv_api_key_encrypted_check'
      AND conrelid = 'public.profiles'::regclass
  ) THEN
    ALTER TABLE public.profiles
      ADD CONSTRAINT profiles_deriv_api_key_encrypted_check
      CHECK (
        deriv_api_key IS NULL
        OR deriv_api_key = ''
        OR deriv_api_key LIKE 'enc:v1:%'
      ) NOT VALID;
  END IF;
END $$;


