-- Enable Row Level Security
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;

-- Profiles: Users can only see/edit their own profile
CREATE POLICY "Users can view own profile" 
ON profiles FOR SELECT 
USING (auth.uid() = id);

CREATE POLICY "Users can update own profile" 
ON profiles FOR UPDATE 
USING (auth.uid() = id);

-- Trades: Users can only see/edit their own trades
CREATE POLICY "Users can view own trades" 
ON trades FOR SELECT 
USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own trades" 
ON trades FOR INSERT 
WITH CHECK (auth.uid() = user_id);

-- Admins: Service Role (Bypasses RLS by default, but nice to be explicit if using admin user)
-- Note: Supabase SERVICE_ROLE_KEY bypasses all these policies automatically.
-- These policies are for Authenticated Users (auth.role() = 'authenticated')

-- Ensure no public access
DROP POLICY IF EXISTS "Public profiles access" ON profiles;
DROP POLICY IF EXISTS "Public trades access" ON trades;
