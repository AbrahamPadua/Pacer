-- Pacer's Supabase tables. Run in the Supabase SQL Editor; safe to run again.

-- Learned estimation multiplier and session limit for each task category
CREATE TABLE IF NOT EXISTS category_profiles (
    category_id TEXT PRIMARY KEY,
    duration_multiplier NUMERIC DEFAULT 1.0,
    max_session_mins INT DEFAULT 90,
    preferred_window TEXT DEFAULT 'afternoon', -- 'morning', 'afternoon' or 'evening'
    sample_count INT DEFAULT 0
);

-- Every scheduled task, linked to its Google Calendar block
CREATE TABLE IF NOT EXISTS tasks_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    category_id TEXT REFERENCES category_profiles(category_id),
    user_est_mins INT NOT NULL,
    ai_suggested_mins INT NOT NULL,
    actual_mins_spent INT DEFAULT 0,
    percent_complete INT DEFAULT 0,
    reschedule_count INT DEFAULT 0,
    deadline TIMESTAMPTZ,
    gcal_event_id TEXT,
    status TEXT DEFAULT 'scheduled', -- 'scheduled', 'in_progress' or 'completed'
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Starting categories; the multipliers adjust as finished tasks are logged
INSERT INTO category_profiles (category_id, duration_multiplier, max_session_mins, preferred_window)
VALUES
    ('math_pset', 1.25, 90, 'afternoon'),
    ('cogs_assignment', 1.15, 90, 'afternoon'),
    ('ece_assignment', 1.00, 90, 'afternoon'),
    ('bipn_assignment', 1.00, 90, 'afternoon'),
    ('lab_research', 1.20, 120, 'morning'),
    ('chores_errands', 1.00, 60, 'evening'),
    ('daily_routine', 1.00, 30, 'evening')
ON CONFLICT (category_id) DO NOTHING;
