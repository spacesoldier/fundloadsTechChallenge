-- ============================================================
-- 0) Reset (опционально) — осторожно
-- ============================================================
-- DROP TABLE IF EXISTS fund_load_decisions_stream;
-- DROP TABLE IF EXISTS fund_load_stream_enriched;
-- DROP TABLE IF EXISTS fund_load_raw;
-- DROP TYPE  IF EXISTS load_stream_status;
-- DROP TYPE  IF EXISTS load_stream_class;
-- DROP TYPE  IF EXISTS load_reject_reason;
-- DROP FUNCTION IF EXISTS normalize_amount_text(text);
-- DROP FUNCTION IF EXISTS parse_amount(text);

-- ============================================================
-- 1) Helpers: amount normalization
-- ============================================================
CREATE OR REPLACE FUNCTION normalize_amount_text(txt text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT regexp_replace(
           regexp_replace(txt, '\s+', '', 'g'),     -- remove whitespace
           '^(?:USD)?\$?(?:USD)?',                  -- strip USD/$ prefixes in any combo
           ''
         );
$$;

CREATE OR REPLACE FUNCTION parse_amount(txt text)
RETURNS numeric(12,2)
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT normalize_amount_text(txt)::numeric(12,2);
$$;

-- ============================================================
-- 2) Enums (status, classification, reasons)
-- ============================================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'load_stream_status') THEN
    CREATE TYPE load_stream_status AS ENUM ('PENDING', 'APPROVED', 'DECLINED');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'load_stream_class') THEN
    CREATE TYPE load_stream_class AS ENUM ('CANONICAL', 'DUPLICATE_REPLAY', 'DUPLICATE_CONFLICT');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'load_reject_reason') THEN
    CREATE TYPE load_reject_reason AS ENUM (
      'DAILY_AMOUNT_LIMIT',
      'WEEKLY_AMOUNT_LIMIT',
      'DAILY_ATTEMPT_LIMIT',
      'DUPLICATE_ID_REPLAY',
      'DUPLICATE_ID_CONFLICT'
    );
  END IF;
END$$;

-- ============================================================
-- 3) Staging: raw input with preserved file order
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_load_raw (
  line_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  raw     jsonb NOT NULL
);

-- ============================================================
-- 4) Enriched stream: 1 row per input line (keeps order!)
-- ============================================================
CREATE TABLE IF NOT EXISTS fund_load_stream_enriched (
  line_no      bigint PRIMARY KEY, -- == fund_load_raw.line_no (input order)
  id_text      text   NOT NULL,
  id_num       bigint NOT NULL,

  customer_id_text text   NOT NULL,
  customer_id      bigint NOT NULL,

  load_amount_raw  text        NOT NULL,
  load_amount      numeric(12,2) NOT NULL,

  event_time   timestamptz NOT NULL,
  raw_json     jsonb       NOT NULL,

  -- classification of this input line
  stream_class load_stream_class NOT NULL,

  -- link to canonical row for same id (for duplicates)
  canonical_line_no bigint NOT NULL,

  -- payload fingerprint (without id) to detect replay vs conflict
  payload_fp        text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stream_enriched_id ON fund_load_stream_enriched (id_num);
CREATE INDEX IF NOT EXISTS idx_stream_enriched_customer_time ON fund_load_stream_enriched (customer_id, event_time);

-- ============================================================
-- 5) Decisions table keyed by line_no (so you can output in original order)
-- ============================================================
DROP TABLE IF EXISTS fund_load_decisions_stream;

CREATE TABLE fund_load_decisions_stream (
  line_no      bigint PRIMARY KEY,   -- 1 decision per input line (preserves order)
  id_num       bigint NOT NULL,
  customer_id  bigint NOT NULL,
  event_time   timestamptz NOT NULL,

  load_amount      numeric(12,2) NOT NULL,
  effective_amount numeric(12,2) NOT NULL,

  day_key_utc  date NOT NULL,
  week_key_utc date NOT NULL,

  -- window snapshots BEFORE
  day_attempts_before integer       NOT NULL,
  day_amount_before   numeric(12,2) NOT NULL,
  week_amount_before  numeric(12,2) NOT NULL,

  -- window snapshots AFTER
  day_attempts_after  integer       NOT NULL,
  day_amount_after    numeric(12,2) NOT NULL,
  week_amount_after   numeric(12,2) NOT NULL,

  status    load_stream_status NOT NULL DEFAULT 'PENDING',
  reasons   load_reject_reason[] NOT NULL DEFAULT '{}',

  canonical_line_no bigint NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now(),
  raw_json   jsonb NOT NULL
);

CREATE INDEX idx_decisions_customer_time ON fund_load_decisions_stream (customer_id, event_time);
CREATE INDEX idx_decisions_day ON fund_load_decisions_stream (customer_id, day_key_utc);
CREATE INDEX idx_decisions_week ON fund_load_decisions_stream (customer_id, week_key_utc);


-- ============================================================
-- 6) LOAD RAW DATA (psql only)
--    Put your input file path here:
-- ============================================================
-- \copy fund_load_raw(raw) FROM '/absolute/path/to/input.txt'

-- ============================================================
-- 7) Build enriched stream (classify canonical vs replay vs conflict)
--    Canonical is "earliest by event_time, tie-breaker by line_no".
-- ============================================================
WITH base AS (
  SELECT
    r.line_no,
    r.raw->>'id'          AS id_text,
    (r.raw->>'id')::bigint AS id_num,

    r.raw->>'customer_id' AS customer_id_text,
    (r.raw->>'customer_id')::bigint AS customer_id,

    r.raw->>'load_amount' AS load_amount_raw,
    parse_amount(r.raw->>'load_amount') AS load_amount,

    (r.raw->>'time')::timestamptz AS event_time,
    r.raw AS raw_json,

    md5(
      (r.raw->>'customer_id') || '|' ||
      ((r.raw->>'time')::timestamptz)::text || '|' ||
      parse_amount(r.raw->>'load_amount')::text
    ) AS payload_fp
  FROM fund_load_raw r
),
ranked AS (
  SELECT
    b.*,

    row_number() OVER (
      PARTITION BY b.id_num
      ORDER BY b.event_time ASC, b.line_no ASC
    ) AS rn,

    first_value(b.line_no) OVER (
      PARTITION BY b.id_num
      ORDER BY b.event_time ASC, b.line_no ASC
    ) AS canonical_line_no,

    first_value(b.payload_fp) OVER (
      PARTITION BY b.id_num
      ORDER BY b.event_time ASC, b.line_no ASC
    ) AS canonical_payload_fp
  FROM base b
)
INSERT INTO fund_load_stream_enriched (
  line_no,
  id_text, id_num,
  customer_id_text, customer_id,
  load_amount_raw, load_amount,
  event_time,
  raw_json,
  stream_class,
  canonical_line_no,
  payload_fp
)
SELECT
  line_no,
  id_text, id_num,
  customer_id_text, customer_id,
  load_amount_raw, load_amount,
  event_time,
  raw_json,

  CASE
    WHEN rn = 1 THEN 'CANONICAL'::load_stream_class
    WHEN payload_fp = canonical_payload_fp THEN 'DUPLICATE_REPLAY'::load_stream_class
    									   ELSE 'DUPLICATE_CONFLICT'::load_stream_class
  END AS stream_class,

  canonical_line_no,
  payload_fp
FROM ranked
ON CONFLICT (line_no) DO NOTHING;

-- ============================================================
-- 8) Seed decisions:
--    - duplicates/conflicts are DECLINED immediately (bypass windows/policies)
--    - canonicals stay PENDING (you will fill them with your engine)
-- ============================================================
-- Week start = Monday, UTC (ISO)
CREATE OR REPLACE FUNCTION week_start_utc(ts timestamptz)
RETURNS date
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT ((ts AT TIME ZONE 'UTC')::date - ((EXTRACT(ISODOW FROM (ts AT TIME ZONE 'UTC'))::int) - 1));
$$;

-- Daily attempts: counts ALL canonical attempts regardless of amount/decision (per requirement)
CREATE TABLE IF NOT EXISTS window_daily_attempts (
  customer_id bigint NOT NULL,
  day_key_utc date   NOT NULL,
  attempts    integer NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, day_key_utc)
);

-- Daily accepted amount (USD)
CREATE TABLE IF NOT EXISTS window_daily_amount_accepted (
  customer_id bigint NOT NULL,
  day_key_utc date   NOT NULL,
  amount_sum  numeric(12,2) NOT NULL DEFAULT 0.00,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, day_key_utc)
);

-- Weekly accepted amount (USD)
CREATE TABLE IF NOT EXISTS window_weekly_amount_accepted (
  customer_id bigint NOT NULL,
  week_key_utc date  NOT NULL,
  amount_sum   numeric(12,2) NOT NULL DEFAULT 0.00,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, week_key_utc)
);


INSERT INTO fund_load_decisions_stream (
  line_no, id_num, customer_id, event_time,
  load_amount, effective_amount,
  day_key_utc, week_key_utc,
  day_attempts_before, day_amount_before, week_amount_before,
  day_attempts_after,  day_amount_after,  week_amount_after,
  status, reasons,
  canonical_line_no,
  raw_json
)
SELECT
  e.line_no,
  e.id_num,
  e.customer_id,
  e.event_time,
  e.load_amount,
  e.load_amount AS effective_amount,

  (e.event_time AT TIME ZONE 'UTC')::date AS day_key_utc,
  week_start_utc(e.event_time) AS week_key_utc,

  -- snapshots default to 0 at seeding; canonicals will be overwritten by engine
  0, 0.00, 0.00,
  0, 0.00, 0.00,

  CASE
    WHEN e.stream_class = 'CANONICAL' THEN 'PENDING'::load_stream_status
    ELSE 'DECLINED'::load_stream_status
  END AS status,

  CASE
    WHEN e.stream_class = 'DUPLICATE_REPLAY' THEN ARRAY['DUPLICATE_ID_REPLAY']::load_reject_reason[]
    WHEN e.stream_class = 'DUPLICATE_CONFLICT' THEN ARRAY['DUPLICATE_ID_CONFLICT']::load_reject_reason[]
    ELSE ARRAY[]::load_reject_reason[]
  END AS reasons,

  e.canonical_line_no,
  e.raw_json
FROM fund_load_stream_enriched e
ON CONFLICT (line_no) DO NOTHING;

-- ============================================================
-- 9) (Optional) Quick checks
-- ============================================================
-- 9.1: everything got a decision row?
-- SELECT
--   (SELECT COUNT(*) FROM fund_load_raw) AS raw_cnt,
--   (SELECT COUNT(*) FROM fund_load_decisions_stream) AS decisions_cnt;

-- 9.2: how many conflicts?
-- SELECT COUNT(*) FROM fund_load_stream_enriched WHERE stream_class = 'DUPLICATE_CONFLICT';

-- ============================================================
-- 10) How to "reconstruct original order":
-- ============================================================
-- SELECT line_no, raw_json, status, reasons
-- FROM fund_load_decisions_stream
-- ORDER BY line_no ASC;



-- ============================================================
-- 11) Process canonicals in stream order (line_no)
-- ============================================================

DO $$
DECLARE
  v_daily_amount_limit  numeric(12,2) := 5000.00;
  v_weekly_amount_limit numeric(12,2) := 20000.00;
  v_daily_attempt_limit integer := 3;

  rec record;

  -- window state before/after
  d_attempts_before integer;
  d_amount_before   numeric(12,2);
  w_amount_before   numeric(12,2);

  d_attempts_after integer;
  d_amount_after   numeric(12,2);
  w_amount_after   numeric(12,2);

  v_status load_stream_status;
  v_reasons load_reject_reason[];

BEGIN
  FOR rec IN
    SELECT
      d.line_no, d.id_num, d.customer_id, d.event_time,
      d.load_amount, d.effective_amount,
      d.day_key_utc, d.week_key_utc,
      d.raw_json
    FROM fund_load_decisions_stream d
    JOIN fund_load_stream_enriched e ON e.line_no = d.line_no
    WHERE e.stream_class = 'CANONICAL'
      AND d.status = 'PENDING'
    ORDER BY d.line_no ASC
  LOOP
    -- --- Read current windows (before)
    SELECT attempts INTO d_attempts_before
    FROM window_daily_attempts
    WHERE customer_id = rec.customer_id AND day_key_utc = rec.day_key_utc;

    IF d_attempts_before IS NULL THEN d_attempts_before := 0; END IF;

    SELECT amount_sum INTO d_amount_before
    FROM window_daily_amount_accepted
    WHERE customer_id = rec.customer_id AND day_key_utc = rec.day_key_utc;

    IF d_amount_before IS NULL THEN d_amount_before := 0.00; END IF;

    SELECT amount_sum INTO w_amount_before
    FROM window_weekly_amount_accepted
    WHERE customer_id = rec.customer_id AND week_key_utc = rec.week_key_utc;

    IF w_amount_before IS NULL THEN w_amount_before := 0.00; END IF;

    -- --- Apply policies (attempt limit is checked as "this attempt would be N+1")
    v_status := 'APPROVED';
    v_reasons := ARRAY[]::load_reject_reason[];

    -- daily attempts: max 3 attempts/day regardless of amount
    IF (d_attempts_before + 1) > v_daily_attempt_limit THEN
      v_status := 'DECLINED';
      v_reasons := v_reasons || 'DAILY_ATTEMPT_LIMIT'::load_reject_reason;
    END IF;

    -- daily amount: max 5000/day (counting accepted amounts)
    IF v_status = 'APPROVED' AND (d_amount_before + rec.effective_amount) > v_daily_amount_limit THEN
      v_status := 'DECLINED';
      v_reasons := v_reasons || 'DAILY_AMOUNT_LIMIT'::load_reject_reason;
    END IF;

    -- weekly amount: max 20000/week (counting accepted amounts)
    IF v_status = 'APPROVED' AND (w_amount_before + rec.effective_amount) > v_weekly_amount_limit THEN
      v_status := 'DECLINED';
      v_reasons := v_reasons || 'WEEKLY_AMOUNT_LIMIT'::load_reject_reason;
    END IF;

    -- --- Commit windows
    -- Attempts are committed regardless of approval (per requirement)
    INSERT INTO window_daily_attempts (customer_id, day_key_utc, attempts, updated_at)
    VALUES (rec.customer_id, rec.day_key_utc, 1, now())
    ON CONFLICT (customer_id, day_key_utc) DO UPDATE
      SET attempts = window_daily_attempts.attempts + 1,
          updated_at = now();

    d_attempts_after := d_attempts_before + 1;

    -- Amount windows commit only if APPROVED
    IF v_status = 'APPROVED' THEN
      INSERT INTO window_daily_amount_accepted (customer_id, day_key_utc, amount_sum, updated_at)
      VALUES (rec.customer_id, rec.day_key_utc, rec.effective_amount, now())
      ON CONFLICT (customer_id, day_key_utc) DO UPDATE
        SET amount_sum = window_daily_amount_accepted.amount_sum + EXCLUDED.amount_sum,
            updated_at = now();

      INSERT INTO window_weekly_amount_accepted (customer_id, week_key_utc, amount_sum, updated_at)
      VALUES (rec.customer_id, rec.week_key_utc, rec.effective_amount, now())
      ON CONFLICT (customer_id, week_key_utc) DO UPDATE
        SET amount_sum = window_weekly_amount_accepted.amount_sum + EXCLUDED.amount_sum,
            updated_at = now();

      d_amount_after := d_amount_before + rec.effective_amount;
      w_amount_after := w_amount_before + rec.effective_amount;
    ELSE
      d_amount_after := d_amount_before;
      w_amount_after := w_amount_before;
    END IF;

    -- --- Persist decision with snapshots
    UPDATE fund_load_decisions_stream
    SET
      day_attempts_before = d_attempts_before,
      day_amount_before   = d_amount_before,
      week_amount_before  = w_amount_before,

      day_attempts_after  = d_attempts_after,
      day_amount_after    = d_amount_after,
      week_amount_after   = w_amount_after,

      status    = v_status,
      reasons   = v_reasons,
      decided_at = now()
    WHERE line_no = rec.line_no;

  END LOOP;
END$$;


-- ============================================================
-- 12) Results output prepare
-- ============================================================
SELECT
  jsonb_build_object(
    'id', id_num::text,
    'customer_id', customer_id::text,
    'accepted', CASE WHEN status = 'APPROVED' THEN 'true' ELSE 'false' END
  ) AS out
FROM fund_load_decisions_stream
ORDER BY line_no ASC;

-- run the following in psql only
\copy (
  SELECT format(
    '{"id":"%s","customer_id":"%s","accepted":%s}',
    id_num::text,
    customer_id::text,
    CASE WHEN status = 'APPROVED' THEN 'true' ELSE 'false' END
  )
  FROM fund_load_decisions_stream
  ORDER BY line_no
) TO 'output.txt';


-- strict formatting
\copy (
  SELECT format(
    '{"id":"%s","customer_id":"%s","accepted":%s}',
    id_num::text,
    customer_id::text,
    CASE WHEN status = 'APPROVED' THEN 'true' ELSE 'false' END
  )
  FROM fund_load_decisions_stream
  ORDER BY line_no
) TO 'output.txt';



-- ============================================================
-- 12) EXPERIMENT RUN: Mondays multiplier + Prime global gate
--     (fresh tables, does not touch baseline)
-- ============================================================

-- ---------- helper: primality test (good enough for 4-6 digit ids)
CREATE OR REPLACE FUNCTION is_prime_bigint(n bigint)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  i bigint;
  lim bigint;
BEGIN
  IF n IS NULL OR n < 2 THEN RETURN false; END IF;
  IF n = 2 THEN RETURN true; END IF;
  IF (n % 2) = 0 THEN RETURN false; END IF;

  lim := floor(sqrt(n::numeric))::bigint;
  i := 3;
  WHILE i <= lim LOOP
    IF (n % i) = 0 THEN RETURN false; END IF;
    i := i + 2;
  END LOOP;

  RETURN true;
END $$;

-- ---------- fresh EXP tables
DROP TABLE IF EXISTS window_daily_attempts_exp_mp;
DROP TABLE IF EXISTS window_daily_amount_accepted_exp_mp;
DROP TABLE IF EXISTS window_weekly_amount_accepted_exp_mp;
DROP TABLE IF EXISTS window_daily_prime_gate_exp_mp;
DROP TABLE IF EXISTS fund_load_decisions_stream_exp_mp;

-- Attempts/day (counts canonical attempts regardless of decision)
CREATE TABLE window_daily_attempts_exp_mp (
  customer_id bigint NOT NULL,
  day_key_utc date   NOT NULL,
  attempts    integer NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, day_key_utc)
);

-- Daily accepted amount (sum of effective_amount for APPROVED)
CREATE TABLE window_daily_amount_accepted_exp_mp (
  customer_id bigint NOT NULL,
  day_key_utc date   NOT NULL,
  amount_sum  numeric(12,2) NOT NULL DEFAULT 0.00,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, day_key_utc)
);

-- Weekly accepted amount (sum of effective_amount for APPROVED)
CREATE TABLE window_weekly_amount_accepted_exp_mp (
  customer_id bigint NOT NULL,
  week_key_utc date  NOT NULL,
  amount_sum   numeric(12,2) NOT NULL DEFAULT 0.00,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, week_key_utc)
);

-- Prime rule is GLOBAL per day (across all clients)
CREATE TABLE window_daily_prime_gate_exp_mp (
  day_key_utc date NOT NULL PRIMARY KEY,
  prime_approved_count integer NOT NULL DEFAULT 0,

  -- optional audit fields: which event "took" the slot first
  first_line_no bigint,
  first_id       bigint,
  first_customer_id bigint,
  first_amount   numeric(12,2),

  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Decisions stream for this experiment (1:1 with input lines, ordered by line_no)
CREATE TABLE fund_load_decisions_stream_exp_mp (
  line_no      bigint PRIMARY KEY,
  id_num       bigint NOT NULL,
  customer_id  bigint NOT NULL,
  event_time   timestamptz NOT NULL,

  load_amount      numeric(12,2) NOT NULL,
  effective_amount numeric(12,2) NOT NULL,

  day_key_utc  date NOT NULL,
  week_key_utc date NOT NULL,

  is_prime boolean NOT NULL,

  -- PRIME global gate snapshot
  prime_used_before integer NOT NULL DEFAULT 0,
  prime_used_after  integer NOT NULL DEFAULT 0,

  -- window snapshots BEFORE
  day_attempts_before integer       NOT NULL DEFAULT 0,
  day_amount_before   numeric(12,2) NOT NULL DEFAULT 0.00,
  week_amount_before  numeric(12,2) NOT NULL DEFAULT 0.00,

  -- window snapshots AFTER
  day_attempts_after  integer       NOT NULL DEFAULT 0,
  day_amount_after    numeric(12,2) NOT NULL DEFAULT 0.00,
  week_amount_after   numeric(12,2) NOT NULL DEFAULT 0.00,

  status    load_stream_status NOT NULL DEFAULT 'PENDING',
  reasons   text[] NOT NULL DEFAULT '{}',

  canonical_line_no bigint NOT NULL,
  raw_json jsonb NOT NULL,

  decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_dec_exp_mp_customer_time ON fund_load_decisions_stream_exp_mp (customer_id, event_time);
CREATE INDEX idx_dec_exp_mp_day ON fund_load_decisions_stream_exp_mp (day_key_utc);
CREATE INDEX idx_dec_exp_mp_week ON fund_load_decisions_stream_exp_mp (week_key_utc);

-- ============================================================
-- 13) Seed EXP decisions from enriched stream (keeps input order)
-- ============================================================

INSERT INTO fund_load_decisions_stream_exp_mp (
  line_no, id_num, customer_id, event_time,
  load_amount, effective_amount,
  day_key_utc, week_key_utc,
  is_prime,
  status, reasons,
  canonical_line_no,
  raw_json
)
SELECT
  e.line_no,
  e.id_num,
  e.customer_id,
  e.event_time,

  e.load_amount,

  -- Monday multiplier: effective = raw * 2 on Monday (UTC), else * 1
  (e.load_amount * CASE
     WHEN EXTRACT(ISODOW FROM (e.event_time AT TIME ZONE 'UTC')) = 1 THEN 2
     ELSE 1
   END)::numeric(12,2) AS effective_amount,

  (e.event_time AT TIME ZONE 'UTC')::date AS day_key_utc,
  week_start_utc(e.event_time)           AS week_key_utc,

  is_prime_bigint(e.id_num) AS is_prime,

  CASE
    WHEN e.stream_class = 'CANONICAL' THEN 'PENDING'::load_stream_status
    ELSE 'DECLINED'::load_stream_status
  END AS status,

  CASE
    WHEN e.stream_class = 'DUPLICATE_REPLAY' THEN ARRAY['DUPLICATE_ID_REPLAY']
    WHEN e.stream_class = 'DUPLICATE_CONFLICT' THEN ARRAY['DUPLICATE_ID_CONFLICT']
    ELSE ARRAY[]::text[]
  END AS reasons,

  e.canonical_line_no,
  e.raw_json
FROM fund_load_stream_enriched e
ON CONFLICT (line_no) DO NOTHING;

-- ============================================================
-- 14) Process canonicals for EXP (attempts + effective sums + prime global gate)
-- ============================================================

DO $$
DECLARE
  v_daily_amount_limit  numeric(12,2) := 5000.00;
  v_weekly_amount_limit numeric(12,2) := 20000.00;
  v_daily_attempt_limit integer := 3;

  v_prime_amount_cap    numeric(12,2) := 9999.00;  -- task: max amount for prime id
  v_prime_global_per_day integer := 1;             -- task: only one per day across all clients

  rec record;

  -- window state before/after
  d_attempts_before integer;
  d_amount_before   numeric(12,2);
  w_amount_before   numeric(12,2);
  p_used_before     integer;

  d_attempts_after integer;
  d_amount_after   numeric(12,2);
  w_amount_after   numeric(12,2);
  p_used_after     integer;

  v_status  load_stream_status;
  v_reasons text[];
BEGIN
  FOR rec IN
    SELECT *
    FROM fund_load_decisions_stream_exp_mp
    WHERE status = 'PENDING'
    ORDER BY line_no
  LOOP
    -- Read window snapshots BEFORE (EXP tables)
    SELECT attempts INTO d_attempts_before
    FROM window_daily_attempts_exp_mp
    WHERE customer_id = rec.customer_id AND day_key_utc = rec.day_key_utc;
    IF d_attempts_before IS NULL THEN d_attempts_before := 0; END IF;

    SELECT amount_sum INTO d_amount_before
    FROM window_daily_amount_accepted_exp_mp
    WHERE customer_id = rec.customer_id AND day_key_utc = rec.day_key_utc;
    IF d_amount_before IS NULL THEN d_amount_before := 0.00; END IF;

    SELECT amount_sum INTO w_amount_before
    FROM window_weekly_amount_accepted_exp_mp
    WHERE customer_id = rec.customer_id AND week_key_utc = rec.week_key_utc;
    IF w_amount_before IS NULL THEN w_amount_before := 0.00; END IF;

    SELECT prime_approved_count INTO p_used_before
    FROM window_daily_prime_gate_exp_mp
    WHERE day_key_utc = rec.day_key_utc;
    IF p_used_before IS NULL THEN p_used_before := 0; END IF;

    -- Apply policies (first-failure reason only; output does not include reasons anyway)
    v_status := 'APPROVED';
    v_reasons := ARRAY[]::text[];

    -- Attempts/day: counted regardless of outcome (attempt N+1)
    IF (d_attempts_before + 1) > v_daily_attempt_limit THEN
      v_status := 'DECLINED';
      v_reasons := v_reasons || ARRAY['DAILY_ATTEMPT_LIMIT'];
    END IF;

    -- Prime global gate (only if still approved so far)
    IF v_status = 'APPROVED' AND rec.is_prime THEN
      IF rec.effective_amount > v_prime_amount_cap THEN
        v_status := 'DECLINED';
        v_reasons := v_reasons || ARRAY['PRIME_AMOUNT_CAP'];
      ELSIF p_used_before >= v_prime_global_per_day THEN
        v_status := 'DECLINED';
        v_reasons := v_reasons || ARRAY['PRIME_DAILY_GLOBAL_LIMIT'];
      END IF;
    END IF;

    -- Daily amount (effective sums)
    IF v_status = 'APPROVED' AND (d_amount_before + rec.effective_amount) > v_daily_amount_limit THEN
      v_status := 'DECLINED';
      v_reasons := v_reasons || ARRAY['DAILY_AMOUNT_LIMIT'];
    END IF;

    -- Weekly amount (effective sums)
    IF v_status = 'APPROVED' AND (w_amount_before + rec.effective_amount) > v_weekly_amount_limit THEN
      v_status := 'DECLINED';
      v_reasons := v_reasons || ARRAY['WEEKLY_AMOUNT_LIMIT'];
    END IF;

    -- Commit attempts ALWAYS for canonical attempt
    INSERT INTO window_daily_attempts_exp_mp(customer_id, day_key_utc, attempts)
    VALUES (rec.customer_id, rec.day_key_utc, 1)
    ON CONFLICT (customer_id, day_key_utc)
    DO UPDATE SET attempts = window_daily_attempts_exp_mp.attempts + 1,
                  updated_at = now();

    d_attempts_after := d_attempts_before + 1;

    -- Commit accepted sums only if APPROVED
    IF v_status = 'APPROVED' THEN
      INSERT INTO window_daily_amount_accepted_exp_mp(customer_id, day_key_utc, amount_sum)
      VALUES (rec.customer_id, rec.day_key_utc, rec.effective_amount)
      ON CONFLICT (customer_id, day_key_utc)
      DO UPDATE SET amount_sum = window_daily_amount_accepted_exp_mp.amount_sum + EXCLUDED.amount_sum,
                    updated_at = now();

      INSERT INTO window_weekly_amount_accepted_exp_mp(customer_id, week_key_utc, amount_sum)
      VALUES (rec.customer_id, rec.week_key_utc, rec.effective_amount)
      ON CONFLICT (customer_id, week_key_utc)
      DO UPDATE SET amount_sum = window_weekly_amount_accepted_exp_mp.amount_sum + EXCLUDED.amount_sum,
                    updated_at = now();

      -- Prime global gate commit only if APPROVED and prime
      IF rec.is_prime THEN
        INSERT INTO window_daily_prime_gate_exp_mp(day_key_utc, prime_approved_count, first_line_no, first_id, first_customer_id, first_amount)
        VALUES (rec.day_key_utc, 1, rec.line_no, rec.id_num, rec.customer_id, rec.effective_amount)
        ON CONFLICT (day_key_utc)
        DO UPDATE SET
          prime_approved_count = window_daily_prime_gate_exp_mp.prime_approved_count + 1,
          updated_at = now();
      END IF;
    END IF;

    -- Read AFTER snapshots (or compute deterministically)
    SELECT amount_sum INTO d_amount_after
    FROM window_daily_amount_accepted_exp_mp
    WHERE customer_id = rec.customer_id AND day_key_utc = rec.day_key_utc;
    IF d_amount_after IS NULL THEN d_amount_after := d_amount_before; END IF;

    SELECT amount_sum INTO w_amount_after
    FROM window_weekly_amount_accepted_exp_mp
    WHERE customer_id = rec.customer_id AND week_key_utc = rec.week_key_utc;
    IF w_amount_after IS NULL THEN w_amount_after := w_amount_before; END IF;

    SELECT prime_approved_count INTO p_used_after
    FROM window_daily_prime_gate_exp_mp
    WHERE day_key_utc = rec.day_key_utc;
    IF p_used_after IS NULL THEN p_used_after := p_used_before; END IF;

    -- Persist decision + snapshots
    UPDATE fund_load_decisions_stream_exp_mp
    SET
      day_attempts_before = d_attempts_before,
      day_amount_before   = d_amount_before,
      week_amount_before  = w_amount_before,

      day_attempts_after  = d_attempts_after,
      day_amount_after    = d_amount_after,
      week_amount_after   = w_amount_after,

      prime_used_before   = p_used_before,
      prime_used_after    = p_used_after,

      status              = v_status,
      reasons             = v_reasons,
      decided_at          = now()
    WHERE line_no = rec.line_no;
  END LOOP;
END $$;

-- ============================================================
-- 15) Export EXP output (keeps input order)
-- ============================================================

\copy (
  SELECT format(
    '{"id":"%s","customer_id":"%s","accepted":%s}',
    id_num::text,
    customer_id::text,
    CASE WHEN status = 'APPROVED' THEN 'true' ELSE 'false' END
  )
  FROM fund_load_decisions_stream_exp_mp
  ORDER BY line_no
) TO 'output_exp_mp.txt';



-- ============================================================
-- 16) Diffs and checks between baseline and experiment
-- ============================================================

-- check mondays effect presence:
select * from fund_load_decisions_stream_exp_mp
where effective_amount != load_amount 

-- how many rows changed acceptance status
SELECT
  count(*) AS total_rows,
  sum(CASE WHEN (b.status='APPROVED') <> (e.status='APPROVED') THEN 1 ELSE 0 END) AS changed_rows
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no);


-- status switch matrix (Approved→Declined, Declined→Approved)
SELECT
  (b.status='APPROVED') AS baseline_accepted,
  (e.status='APPROVED') AS exp_accepted,
  count(*) AS cnt
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no)
GROUP BY 1,2
ORDER BY 1,2;


-- first N rows changed
SELECT
  b.line_no,
  b.id_num,
  b.customer_id,
  b.event_time,
  b.load_amount,
  (b.status='APPROVED') AS baseline_accepted,
  (e.status='APPROVED') AS exp_accepted,
  e.effective_amount,
  e.is_prime,
  e.reasons
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no)
WHERE (b.status='APPROVED') <> (e.status='APPROVED')
ORDER BY b.line_no
LIMIT 50;

-- top reasons for declines during the experiment
SELECT
  r AS reason,
  count(*) AS cnt
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no)
CROSS JOIN LATERAL unnest(e.reasons) AS r
WHERE (b.status='APPROVED') <> (e.status='APPROVED')
GROUP BY 1
ORDER BY cnt DESC, reason;


-- monday's declines
SELECT
  count(*) AS changed_on_monday
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no)
WHERE (b.status='APPROVED') <> (e.status='APPROVED')
  AND EXTRACT(ISODOW FROM (e.event_time AT TIME ZONE 'UTC')) = 1;

-- prime IDs declines
SELECT
  count(*) AS changed_for_prime_ids
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no)
WHERE (b.status='APPROVED') <> (e.status='APPROVED')
  AND e.is_prime;


-- prime IDs list
select id_num, is_prime
from fund_load_decisions_stream_exp_mp
where is_prime is false
order by id_num desc

-- decisions diff as view
CREATE OR REPLACE VIEW decision_diff_exp_mp_v AS
SELECT
  b.line_no,
  b.id_num,
  b.customer_id,
  b.event_time,
  b.load_amount,
  (b.status='APPROVED') AS baseline_accepted,
  (e.status='APPROVED') AS exp_accepted,
  e.effective_amount,
  e.is_prime,
  e.reasons AS exp_reasons
FROM fund_load_decisions_stream b
JOIN fund_load_decisions_stream_exp_mp e USING (line_no)
WHERE (b.status='APPROVED') <> (e.status='APPROVED');




