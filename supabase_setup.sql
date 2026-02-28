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


