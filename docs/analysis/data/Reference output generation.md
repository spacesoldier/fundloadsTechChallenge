# Input Data Workup & Reference Output Generation

This document describes how we prepared the provided input dataset, handled known data-quality traps, preserved the original record order, and produced a deterministic “reference” output set (approved/declined decisions) in the exact format required by the challenge.

> **Goal**: build a reproducible, auditable pipeline from raw input → normalized events → deduplication/idempotency classification → decisioning with windowed limits → ordered output.

The full version of the script which performs all the steps described below is located [right there](scripts/generate_ref_outs.sql).
All the operations with input dataset are performed using Postgresql 18 which runs as a [docker-compose](scripts/docker-compose.yml) setup.

---

## 0. Principles and guarantees

We intentionally designed the data workup around these guarantees:

1. **Reproducibility**  
   Same input file + same rules → same output.

2. **Auditability**  
   Every input line is traceable through the pipeline, including invalid/duplicate/conflict cases.

3. **Determinism under dirty data**  
   Dataset contains formatting inconsistencies and duplicate identifiers. These are handled explicitly and deterministically.

4. **Order preservation**  
   The output is emitted **in the same order as the input file**.

---

## 1. Staging the raw input

### 1.1 Why we used a staging table
The dataset is provided as line-delimited JSON. We load it into a staging table as-is to:
- preserve raw payload
- preserve line order
- allow SQL-based inspection and test-case extraction

### 1.2 Preserving input order (`line_no`)
Relational tables do not have a “physical position” of records. To preserve and later reconstruct the original sequence, we store the input as:

- `raw` (jsonb): the original JSON payload
- `line_no` (identity/bigserial): generated in insertion order during `\copy`

This makes the input order explicit and stable.

### 1.3 Reading input

```sql
-- ============================================================
-- 3) Staging: raw input with preserved file order
-- ============================================================

CREATE TABLE IF NOT EXISTS fund_load_raw (
	line_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	raw jsonb NOT NULL
);

-- copy input.txt into fund_load_raw(raw) - runs in psql client only
\copy fund_load_raw(raw) FROM '/absolute/path/to/input.txt'
```


---

## 2. Normalizing money amounts (dirty currency formats)

### 2.1 Why normalization is required

The dataset includes multiple “currency prefix” variants, including intentionally inconsistent forms:

- `USD1234.00`
- `$1234.00`
- `USD$1234.00`

Naive parsing into `numeric` fails on values like `USD$431.04`.

### 2.2 Approach

We introduced a canonicalization function to normalize the text to a strict numeric string:
- remove whitespace
- strip optional leading `USD`, `$`, and even combined `USD$` in either order
- parse normalized string to `numeric(12,2)`
- validate normalized format with a CHECK constraint

This yields:
- **raw amount preserved** for audit (`load_amount_raw`)
- **parsed numeric amount** for calculations (`load_amount`)

### 2.3 Normalize amounts script

```sql
CREATE OR REPLACE FUNCTION normalize_amount_text(txt text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
	SELECT regexp_replace(
				regexp_replace(txt, '\s+', '', 'g'), -- remove whitespace
				'^(?:USD)?\$?(?:USD)?', -- strip USD/$ prefixes in any combo
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
```

---

## 3. Duplicate identifiers and the idempotency gate

### 3.1 What we observed

The input dataset contains repeated `id` values. Some repeats are exact payload replays; others are **conflicts** (same `id` with different amounts and/or other fields).

During workup we detected:
- **16 duplicate-conflict cases** (same `id`, different payload)

This is treated as an explicit dataset characteristic (likely intentional).

### 3.2 Semantics we enforce

We treat `id` as an **idempotency key**:
- A given `id` should correspond to a single canonical attempt.
- Any non-canonical occurrences are not fed into window/policy evaluation.
- They are explicitly recorded and classified for audit.

### 3.3 Canonicalization rule

We choose a canonical record per `id` deterministically:
- Primary: **earliest by `event_time`**
- Tie-breaker: **earliest by `line_no`** (first occurrence in file)

### 3.4 Replay vs conflict classification

For non-canonical duplicates:
- **DUPLICATE_REPLAY**: payload fingerprint equals canonical fingerprint
- **DUPLICATE_CONFLICT**: payload differs from canonical (e.g., different amount)

A “payload fingerprint” is computed excluding the `id` itself, typically using:
- `customer_id`    
- `event_time`
- normalized amount

### 3.5 Stream with classification creation

```sql
-- ============================================================
-- 4) Enriched stream: 1 row per input line (keeps order!)
-- ============================================================

CREATE TABLE IF NOT EXISTS fund_load_stream_enriched (
	line_no bigint PRIMARY KEY, -- == fund_load_raw.line_no (input order)
	id_text text NOT NULL,
	id_num bigint NOT NULL,
	
	customer_id_text text NOT NULL,
	customer_id bigint NOT NULL,
	
	load_amount_raw text NOT NULL,
	load_amount numeric(12,2) NOT NULL,
	
	event_time timestamptz NOT NULL,
	raw_json jsonb NOT NULL,
	
	-- classification of this input line
	stream_class load_stream_class NOT NULL,
	
	-- link to canonical row for same id (for duplicates)
	canonical_line_no bigint NOT NULL,
	
	-- payload fingerprint (without id) to detect replay vs conflict
	payload_fp text NOT NULL
);


CREATE INDEX IF NOT EXISTS idx_stream_enriched_id ON fund_load_stream_enriched (id_num);

CREATE INDEX IF NOT EXISTS idx_stream_enriched_customer_time ON fund_load_stream_enriched (customer_id, event_time);

  

```

---

## 4. Reconstructable stream: one row per input line

### 4.1 Why an “enriched stream” table exists

We want a 1:1 mapping between input lines and downstream decisions.  
Therefore we create an enriched stream table keyed by `line_no`:
- contains parsed and normalized fields
- contains classification (`CANONICAL`, `DUPLICATE_REPLAY`, `DUPLICATE_CONFLICT`)
- contains `canonical_line_no` pointer

This allows us to:
- keep the original ordering
- keep rejected duplicates “in their original position” in the stream
- produce output strictly in file order

### 4.2 Seed enriched stream

```sql

CREATE TABLE IF NOT EXISTS fund_load_stream_enriched (
	line_no bigint PRIMARY KEY, -- == fund_load_raw.line_no (input order)
	id_text text NOT NULL,
	id_num bigint NOT NULL,

	customer_id_text text NOT NULL,
	customer_id bigint NOT NULL,

	load_amount_raw text NOT NULL,
	load_amount numeric(12,2) NOT NULL,

	event_time timestamptz NOT NULL,
	raw_json jsonb NOT NULL,
  

	-- classification of this input line
	stream_class load_stream_class NOT NULL,

  
	-- link to canonical row for same id (for duplicates)
	canonical_line_no bigint NOT NULL,

	-- payload fingerprint (without id) to detect replay vs conflict
	payload_fp text NOT NULL
);

  

CREATE INDEX IF NOT EXISTS idx_stream_enriched_id ON fund_load_stream_enriched (id_num);

CREATE INDEX IF NOT EXISTS idx_stream_enriched_customer_time ON fund_load_stream_enriched (customer_id, event_time);



-- ============================================================
-- 7) Build enriched stream (classify canonical vs replay vs conflict)
-- Canonical is "earliest by event_time, tie-breaker by line_no".
-- ============================================================

WITH base AS (

	SELECT
		r.line_no,
		r.raw->>'id' AS id_text,
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

```

---

## 5. Decision stream table (output is produced from here)

### 5.1 Why decisions are keyed by `line_no`

The challenge expects an output record per input record, in the same order.  
We therefore store decisions keyed by `line_no`:
- one decision per input line
- duplicates/conflicts do not “disappear”; they get explicit declined decisions
- canonical records are evaluated using windowed policies

### 5.2 PENDING / APPROVED / DECLINED

We seed the decision table:
- `CANONICAL` → `PENDING` (to be evaluated)
- `DUPLICATE_*` → `DECLINED` immediately, with reason

### 5.3 Seed decisions

```sql
-- ============================================================
-- 5) Decisions table keyed by line_no (so you can output in original order)
-- ============================================================
DROP TABLE IF EXISTS fund_load_decisions_stream;

CREATE TABLE fund_load_decisions_stream (
	line_no bigint PRIMARY KEY, -- 1 decision per input line (preserves order)
	id_num bigint NOT NULL,
	customer_id bigint NOT NULL,
	event_time timestamptz NOT NULL,
	
	load_amount numeric(12,2) NOT NULL,
	effective_amount numeric(12,2) NOT NULL,
	
	day_key_utc date NOT NULL,
	week_key_utc date NOT NULL,
	
	-- window snapshots BEFORE
	day_attempts_before integer NOT NULL,
	day_amount_before numeric(12,2) NOT NULL,
	week_amount_before numeric(12,2) NOT NULL,
	
	-- window snapshots AFTER
	day_attempts_after integer NOT NULL,
	day_amount_after numeric(12,2) NOT NULL,
	week_amount_after numeric(12,2) NOT NULL,
	
	status load_stream_status NOT NULL DEFAULT 'PENDING',
	reasons load_reject_reason[] NOT NULL DEFAULT '{}',
	
	canonical_line_no bigint NOT NULL,
	decided_at timestamptz NOT NULL DEFAULT now(),
	
	raw_json jsonb NOT NULL
);

CREATE INDEX idx_decisions_customer_time ON fund_load_decisions_stream (customer_id, event_time);

CREATE INDEX idx_decisions_day ON fund_load_decisions_stream (customer_id, day_key_utc);

CREATE INDEX idx_decisions_week ON fund_load_decisions_stream (customer_id, week_key_utc);

-- [SCRIPT: seed decisions (canonicals=PENDING, duplicates=DECLINED + reason)]
INSERT INTO fund_load_decisions_stream (
	line_no, id_num, customer_id, event_time,
	load_amount, effective_amount,
	day_key_utc, week_key_utc,
	day_attempts_before, day_amount_before, week_amount_before,
	day_attempts_after, day_amount_after, week_amount_after,
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

```

---

## 6. Windowed policies and state storage

### 6.1 Policy rules (baseline)

We evaluated canonical attempts under baseline limits:

| Limit Type       | Description                                                                            |
| ---------------- | -------------------------------------------------------------------------------------- |
| Daily Limit      | A customer can load a maximum of $5,000 per day.                                       |
| Weekly Limit     | A customer can load a maximum of $20,000 per week.                                     |
| Daily Load Count | A customer can perform a maximum of 3 load attempts per day, regardless of the amount. |

### 6.2 Key semantic decision: attempts are counted regardless of outcome

We interpret “3 load attempts/day” literally:
- attempts are counted regardless of approved/declined
- therefore a customer can “burn” attempts even on rejected loads

### 6.3 Window state tables

To keep logic explicit and auditable, we store window state separately:
- Daily attempts (counts all canonical attempts)
- Daily accepted amount (sums only APPROVED effective amounts)
- Weekly accepted amount (sums only APPROVED effective amounts)

### 6.4 Time keys

We compute window keys in UTC:
- `day_key_utc`: `(event_time AT TIME ZONE 'UTC')::date`
- `week_key_utc`: start of week in UTC (ISO Monday)

### 6.5 Windowing

```sql
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
	day_key_utc date NOT NULL,
	attempts integer NOT NULL DEFAULT 0,
	updated_at timestamptz NOT NULL DEFAULT now(),
	PRIMARY KEY (customer_id, day_key_utc)
);


-- Daily accepted amount (USD)
CREATE TABLE IF NOT EXISTS window_daily_amount_accepted (
	customer_id bigint NOT NULL,
	day_key_utc date NOT NULL,
	amount_sum numeric(12,2) NOT NULL DEFAULT 0.00,
	updated_at timestamptz NOT NULL DEFAULT now(),
	PRIMARY KEY (customer_id, day_key_utc)
);

  
-- Weekly accepted amount (USD)
CREATE TABLE IF NOT EXISTS window_weekly_amount_accepted (
	customer_id bigint NOT NULL,
	week_key_utc date NOT NULL,
	amount_sum numeric(12,2) NOT NULL DEFAULT 0.00,
	updated_at timestamptz NOT NULL DEFAULT now(),
	PRIMARY KEY (customer_id, week_key_utc)
);
```

---

## 7. Canonical decisioning pass (stream order)

### 7.1 Why we process in `line_no` order

Even though canonicalization uses `event_time`, once canonicals are selected we evaluate them in deterministic stream order (`line_no`) to match the ingestion model and ensure reproducibility.

### 7.2 Snapshotting before/after

For each canonical record we store:
- `*_before`: window values at evaluation time
- `*_after`: window values after committing changes

This creates a “flight recorder” for debugging and makes test-case reasoning transparent.

### 7.3 Commit strategy

- Daily attempts: committed **always** for canonical events
- Amount windows (daily/weekly): committed **only if APPROVED**    

### 7.4 Decision logic

For a canonical event:
1. Check if attempts/day would exceed limit → decline if yes
2. If still approved, check daily amount limit (accepted sums) → decline if exceeded
3. If still approved, check weekly amount limit (accepted sums) → decline if exceeded
4. Commit windows according to strategy above
5. Persist decision and snapshots

### 7.5 Decision logic

```sql
-- ============================================================
-- 11) Process canonicals in stream order (line_no)
-- ============================================================
DO $$
	DECLARE
	v_daily_amount_limit numeric(12,2) := 5000.00;
	v_weekly_amount_limit numeric(12,2) := 20000.00;
	v_daily_attempt_limit integer := 3;
	
	rec record;
	
	-- window state before/after
	d_attempts_before integer;
	d_amount_before numeric(12,2);
	w_amount_before numeric(12,2);
	
	d_attempts_after integer;
	d_amount_after numeric(12,2);
	w_amount_after numeric(12,2);
	
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
				day_amount_before = d_amount_before,
				week_amount_before = w_amount_before,

				day_attempts_after = d_attempts_after,
				day_amount_after = d_amount_after,
				week_amount_after = w_amount_after,

				status = v_status,				
				reasons = v_reasons,
				decided_at = now()
			WHERE line_no = rec.line_no;
				
	END LOOP;

END$$;
```

---

## 8. Handling duplicates/conflicts in the final output stream

### 8.1 Why duplicates/conflicts bypass windows

Non-canonical events are rejected at the idempotency gate and do not participate in velocity windows. This prevents:
- double-counting attempts or amounts
- inconsistent state updates driven by conflicting payloads for the same id

### 8.2 Output presence

Even though they bypass windows, they still produce an output decision row because:
- output must align with input lines 1:1
- audit trail must be complete

### 8.3 Duplicates handling

The actual logic implemented in [[#4.2 Seed enriched stream]], and [[#5.3 Seed decisions]]

---

## 9. Producing the required output format

### 9.1 Output format requirement

The challenge requires output as valid JSON in `output.txt` with fields:
- `id`
- `customer_id`
- `accepted` (boolean)

### 9.2 Ordering requirement

We emit output in the original file order by sorting by `line_no`.

### 9.3 Writing outputs

```sql
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
	SELECT
		jsonb_build_object(
		'id', id_num::text,
		'customer_id', customer_id::text,
		'accepted', (status = 'APPROVED')
	)::text
	FROM fund_load_decisions_stream
	ORDER BY line_no
) TO '/actual/path/to/output.txt';

-- strict formatting keeping the fields order as expected
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
```

---

## 10. Validation checklist (recommended)

Before submitting the output, we recommend validating:

1. **Row count preservation**  
    `count(raw) == count(decisions_stream)`
    
2. **No unresolved canonicals**  
    No `PENDING` status remains for `CANONICAL` rows
    
3. **Duplicates accounted for**  
    Count and inspect `DUPLICATE_CONFLICT` rows (observed: 16)
    
4. **Money parsing correctness**  
    No NULL/invalid numeric values after normalization
    
5. **Deterministic re-run**  
    Re-running from scratch produces byte-identical output

*Note: the following scripts also contain some checks for the extended case results*

### Results checks

```sql
-- ============================================================
-- Validation checklist (recommended)
-- ============================================================

-- 1) Row count preservation
-- raw lines == enriched stream == decision stream (baseline)
SELECT
  (SELECT count(*) FROM fund_load_raw)                  AS raw_cnt,
  (SELECT count(*) FROM fund_load_stream_enriched)      AS enriched_cnt,
  (SELECT count(*) FROM fund_load_decisions_stream)     AS baseline_decisions_cnt,
  (SELECT count(*) FROM fund_load_decisions_stream_exp_mp) AS exp_decisions_cnt;

-- Optional hard assert (uncomment to fail the script if mismatch)
-- DO $$
-- DECLARE
--   c_raw bigint;
--   c_enr bigint;
--   c_base bigint;
--   c_exp bigint;
-- BEGIN
--   SELECT count(*) INTO c_raw FROM fund_load_raw;
--   SELECT count(*) INTO c_enr FROM fund_load_stream_enriched;
--   SELECT count(*) INTO c_base FROM fund_load_decisions_stream;
--   SELECT count(*) INTO c_exp FROM fund_load_decisions_stream_exp_mp;
--   IF c_raw <> c_enr THEN
--     RAISE EXCEPTION 'Count mismatch: raw(%) != enriched(%)', c_raw, c_enr;
--   END IF;
--   IF c_raw <> c_base THEN
--     RAISE EXCEPTION 'Count mismatch: raw(%) != baseline decisions(%)', c_raw, c_base;
--   END IF;
--   IF c_raw <> c_exp THEN
--     RAISE EXCEPTION 'Count mismatch: raw(%) != exp decisions(%)', c_raw, c_exp;
--   END IF;
-- END $$;


-- 2) No unresolved canonicals (baseline + experiment)
-- Baseline: no PENDING should remain at all (only canonicals are processed; duplicates seeded declined)
SELECT count(*) AS baseline_pending
FROM fund_load_decisions_stream
WHERE status = 'PENDING';

SELECT count(*) AS exp_pending
FROM fund_load_decisions_stream_exp_mp
WHERE status = 'PENDING';

-- If you want to ensure ONLY canonicals were processed (and duplicates never left PENDING)
-- (Assumes canonical_line_no = line_no for canonicals, which is typically true in our enriched stream)
SELECT
  count(*) FILTER (WHERE status='PENDING' AND canonical_line_no = line_no) AS pending_canonicals,
  count(*) FILTER (WHERE status='PENDING' AND canonical_line_no <> line_no) AS pending_noncanonicals
FROM fund_load_decisions_stream;

SELECT
  count(*) FILTER (WHERE status='PENDING' AND canonical_line_no = line_no) AS pending_canonicals,
  count(*) FILTER (WHERE status='PENDING' AND canonical_line_no <> line_no) AS pending_noncanonicals
FROM fund_load_decisions_stream_exp_mp;


-- 3) Duplicates accounted for (especially DUPLICATE_CONFLICT)
-- Enriched stream counts
SELECT
  stream_class,
  count(*) AS cnt
FROM fund_load_stream_enriched
GROUP BY stream_class
ORDER BY stream_class;

-- Quick check: expected conflicts count (observed 16 in our dataset)
SELECT count(*) AS duplicate_conflict_cnt
FROM fund_load_stream_enriched
WHERE stream_class = 'DUPLICATE_CONFLICT';

-- Sanity: duplicates should be DECLINED in baseline/experiment
SELECT
  sum(CASE WHEN e.stream_class <> 'CANONICAL' AND b.status <> 'DECLINED' THEN 1 ELSE 0 END) AS baseline_duplicates_not_declined,
  sum(CASE WHEN e.stream_class <> 'CANONICAL' AND x.status <> 'DECLINED' THEN 1 ELSE 0 END) AS exp_duplicates_not_declined
FROM fund_load_stream_enriched e
JOIN fund_load_decisions_stream b        ON b.line_no = e.line_no
JOIN fund_load_decisions_stream_exp_mp x ON x.line_no = e.line_no;


-- 4) Money parsing correctness (normalization succeeded)
-- a) No NULL numeric amounts after normalization (enriched stream)
SELECT count(*) AS null_load_amount_cnt
FROM fund_load_stream_enriched
WHERE load_amount IS NULL;

-- b) Detect suspicious raw formats that still don't match our accepted patterns
-- (Tune this regex to your actual normalization rules.)
SELECT count(*) AS suspicious_amount_format_cnt
FROM fund_load_stream_enriched
WHERE load_amount_raw !~ '^(USD)?(\$)?\d+(\.\d{2})$'   -- accepts: $123.45, USD123.45, USD$123.45
  AND load_amount_raw !~ '^(\$)?(USD)?\d+(\.\d{2})$';  -- accepts: $USD123.45 (if it ever appears)

-- c) Inspect a few suspicious examples
SELECT line_no, load_amount_raw
FROM fund_load_stream_enriched
WHERE load_amount_raw !~ '^(USD)?(\$)?\d+(\.\d{2})$'
  AND load_amount_raw !~ '^(\$)?(USD)?\d+(\.\d{2})$'
ORDER BY line_no
LIMIT 50;


-- 5) Deterministic re-run (practical approach)
-- SQL alone can't prove byte-identical outputs across reruns without storing artifacts.
-- Two pragmatic checks inside DB:
--   A) Stable aggregate "fingerprint" of decisions for baseline/exp
--   B) Stable aggregate "fingerprint" of exported JSON (if you store it)
--
-- A) Baseline fingerprint (order-sensitive)
SELECT
  md5(string_agg(
        (line_no::text || ':' || id_num::text || ':' || customer_id::text || ':' || (status='APPROVED')::text),
        '|' ORDER BY line_no
      )) AS baseline_decisions_fingerprint
FROM fund_load_decisions_stream;

-- A) Experiment fingerprint (order-sensitive)
SELECT
  md5(string_agg(
        (line_no::text || ':' || id_num::text || ':' || customer_id::text || ':' || (status='APPROVED')::text),
        '|' ORDER BY line_no
      )) AS exp_decisions_fingerprint
FROM fund_load_decisions_stream_exp_mp;

-- Tip for true byte-identical file check (outside DB):
--   sha256sum output.txt output_exp_mp.txt
--   rerun from scratch and compare hashes

```

---

## 11. Notes for maintainers / future extensions

This pipeline is structured to support additional policies without changing the data workup fundamentals:

- Adding new risk modifiers (e.g., Monday multiplier) can be done by adjusting `effective_amount`
- Additional windows (rolling windows, per-merchant, per-state) can be represented as new window tables
- Policy configuration can be externalized (YAML/DB) while keeping data workup stable
- Idempotency gate can evolve from “id only” to a richer replay protection model (hashes, signatures, etc.)

---

## 12. Experimental scenario: Mondays multiplier + Prime-ID global gate
### 12.1 Why an experimental scenario is separate

Baseline reference output is treated as a **golden run** and must not be affected by further experimentation.  
Therefore, the experimental run uses a fresh set of tables, leaving baseline tables intact.

We implement this by creating **new tables with a suffix** (example: `_exp_mp`).  
The input/enriched stream is reused as read-only source data.

### 12.2 Scenario definition
#### 12.2.1 Monday multiplier

For Mondays (in UTC), the regulator requires that loads are “counted as double their value”.
We implement this by introducing:

- effective_amount = load_amount * risk_factor
- risk_factor = 2 on Monday (UTC), else 1

All amount windows in this scenario operate on effective_amount.

> This preserves the original load_amount for audit and keeps policy math explicit.


#### 12.2.2 Prime-ID global gate

Prime numbers are considered suspicious. For any load attempt where id is prime:

- Only one prime-ID load may be approved per day across all customers (global per-day gate)
- The load must not exceed $9,999 (applied to effective_amount in this scenario)

This rule requires a global daily window (not per-customer) and is only updated on APPROVED decisions.

### 12.3 Experimental window tables

The experiment uses its own window state tables:
- daily attempts per customer/day (same semantics as baseline)
- daily accepted amount per customer/day (uses effective_amount)
- weekly accepted amount per customer/week (uses effective_amount)
- global daily prime gate (per day, across all customers)

#### Create experimental window tables:
```sql
-- Attempts/day (counts canonical attempts regardless of decision)

CREATE TABLE window_daily_attempts_exp_mp (
	customer_id bigint NOT NULL,
	day_key_utc date NOT NULL,
	attempts integer NOT NULL DEFAULT 0,
	updated_at timestamptz NOT NULL DEFAULT now(),
	PRIMARY KEY (customer_id, day_key_utc)
);
  

-- Daily accepted amount (sum of effective_amount for APPROVED)
CREATE TABLE window_daily_amount_accepted_exp_mp (
	customer_id bigint NOT NULL,
	day_key_utc date NOT NULL,
	amount_sum numeric(12,2) NOT NULL DEFAULT 0.00,
	updated_at timestamptz NOT NULL DEFAULT now(),
	PRIMARY KEY (customer_id, day_key_utc)
);


-- Weekly accepted amount (sum of effective_amount for APPROVED)
CREATE TABLE window_weekly_amount_accepted_exp_mp (
	customer_id bigint NOT NULL,
	week_key_utc date NOT NULL,
	amount_sum numeric(12,2) NOT NULL DEFAULT 0.00,
	updated_at timestamptz NOT NULL DEFAULT now(),
	PRIMARY KEY (customer_id, week_key_utc)
);

  
-- Prime rule is GLOBAL per day (across all clients)
CREATE TABLE window_daily_prime_gate_exp_mp (
	day_key_utc date NOT NULL PRIMARY KEY,
	prime_approved_count integer NOT NULL DEFAULT 0,
	
	-- optional audit fields: which event "took" the slot first
	first_line_no bigint,
	first_id bigint,
	first_customer_id bigint,
	first_amount numeric(12,2),
	
	updated_at timestamptz NOT NULL DEFAULT now()
);
```


### 12.4 Experimental decision stream table

The experiment decision table is also keyed by line_no and keeps 1:1 mapping with input.
- Additional columns are included for the new scenario:
- effective_amount
- is_prime
- prime gate snapshots (prime_used_before/after)
- window snapshots based on effective sums

#### Create decision stream:
```sql
-- Decisions stream for this experiment (1:1 with input lines, ordered by line_no)

CREATE TABLE fund_load_decisions_stream_exp_mp (
	line_no bigint PRIMARY KEY,
	id_num bigint NOT NULL,
	customer_id bigint NOT NULL,
	event_time timestamptz NOT NULL,
	
	load_amount numeric(12,2) NOT NULL,
	effective_amount numeric(12,2) NOT NULL,
	
	day_key_utc date NOT NULL,
	week_key_utc date NOT NULL,
	
	is_prime boolean NOT NULL,
	
	-- PRIME global gate snapshot
	prime_used_before integer NOT NULL DEFAULT 0,
	prime_used_after integer NOT NULL DEFAULT 0,
	
	-- window snapshots BEFORE
	day_attempts_before integer NOT NULL DEFAULT 0,
	day_amount_before numeric(12,2) NOT NULL DEFAULT 0.00,
	week_amount_before numeric(12,2) NOT NULL DEFAULT 0.00,
	
	-- window snapshots AFTER
	day_attempts_after integer NOT NULL DEFAULT 0,
	day_amount_after numeric(12,2) NOT NULL DEFAULT 0.00,
	week_amount_after numeric(12,2) NOT NULL DEFAULT 0.00,
	
	status load_stream_status NOT NULL DEFAULT 'PENDING',
	reasons text[] NOT NULL DEFAULT '{}',
	
	canonical_line_no bigint NOT NULL,
	raw_json jsonb NOT NULL,
	
	decided_at timestamptz NOT NULL DEFAULT now()
);

  

CREATE INDEX idx_dec_exp_mp_customer_time ON fund_load_decisions_stream_exp_mp (customer_id, event_time);

CREATE INDEX idx_dec_exp_mp_day ON fund_load_decisions_stream_exp_mp (day_key_utc);

CREATE INDEX idx_dec_exp_mp_week ON fund_load_decisions_stream_exp_mp (week_key_utc);
```

#### Seed decision stream:
```sql
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
					END
	)::numeric(12,2) AS effective_amount,
	
	(e.event_time AT TIME ZONE 'UTC')::date AS day_key_utc,
	week_start_utc(e.event_time) AS week_key_utc,
	
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
```

### 12.5 Experimental adjudication pass

Canonicals are processed in deterministic stream order (ORDER BY line_no).

Recommended rule order (explicit, deterministic):
1. attempts/day (counts canonical attempts regardless of outcome)
2. prime-ID checks (cap + global daily gate)
3. daily amount limit (effective sums)
4. weekly amount limit (effective sums)

Window commit rules:
- attempts/day: commit ALWAYS for canonicals
- amount windows: commit only if APPROVED
- prime gate: commit only if APPROVED and is_prime=true

#### Experimental adjudication loop:
```sql
-- ============================================================
-- 14) Process canonicals for EXP (attempts + effective sums + prime global gate)
-- ============================================================
DO $$
DECLARE
v_daily_amount_limit numeric(12,2) := 5000.00;
v_weekly_amount_limit numeric(12,2) := 20000.00;
v_daily_attempt_limit integer := 3;

v_prime_amount_cap numeric(12,2) := 9999.00; -- task: max amount for prime id
v_prime_global_per_day integer := 1; -- task: only one per day across all clients

rec record;

-- window state before/after
d_attempts_before integer;
d_amount_before numeric(12,2);
w_amount_before numeric(12,2);
p_used_before integer;

d_attempts_after integer;
d_amount_after numeric(12,2);
w_amount_after numeric(12,2);
p_used_after integer;

v_status load_stream_status;
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
				DO UPDATE 
					SET amount_sum = window_daily_amount_accepted_exp_mp.amount_sum + EXCLUDED.amount_sum,
						updated_at = now();
	
			INSERT INTO window_weekly_amount_accepted_exp_mp(customer_id, week_key_utc, amount_sum)
			VALUES (rec.customer_id, rec.week_key_utc, rec.effective_amount)
			ON CONFLICT (customer_id, week_key_utc)
				DO UPDATE 
					SET amount_sum = window_weekly_amount_accepted_exp_mp.amount_sum + EXCLUDED.amount_sum,
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
			day_amount_before = d_amount_before,
			week_amount_before = w_amount_before,
			  
			day_attempts_after = d_attempts_after,
			day_amount_after = d_amount_after,
			week_amount_after = w_amount_after,
			
			prime_used_before = p_used_before,
			prime_used_after = p_used_after,
			
			status = v_status,
			reasons = v_reasons,
			decided_at = now()
		WHERE line_no = rec.line_no;

	END LOOP;
END $$;
```


### 12.6 Experimental output export

Output is written to `output_exp_mp.txt` as valid JSON.  
Ordering is guaranteed by `ORDER BY line_no`.

Output export:
```sql
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
```


## 13. Diff between baseline and experiment (recommended)

The diff report is useful for:
- validating that new rules changed behavior in expected places
- producing stable “golden diff” fixtures for automated tests
- debugging edge cases (prime gate and Monday multiplier)

### 13.1 Diff metrics (summary)
- how many decisions changed (accepted flipped)
- transition matrix (approved→declined, declined→approved)
- top reasons in experimental run for changed rows

#### Diff queries:
```sql
-- ============================================================
-- 16) Diffs and checks between baseline and experiment
-- ============================================================

-- how many rows acceptance status chamged
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


```


### 13.2 Persisting the diff as a fixture (for tests)

We recommend storing the diff into a dedicated table and exporting it to JSON:

- `decision_diff_exp_mp` table: only rows where baseline != experiment
- `decision_diff_exp_mp.json`: stable golden fixture for tests

#### Persisting diff queries into a table:
```sql
DROP TABLE IF EXISTS decision_diff_exp_mp;

CREATE TABLE decision_diff_exp_mp AS
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
WHERE (b.status='APPROVED') <> (e.status='APPROVED')
ORDER BY b.line_no;

CREATE INDEX idx_decision_diff_exp_mp_line ON decision_diff_exp_mp(line_no);

```

#### Export diffs to json:
```sql
\copy (
  SELECT
    jsonb_agg(
      to_jsonb(t) ORDER BY line_no
    )::text
  FROM decision_diff_exp_mp t
) TO 'decision_diff_exp_mp.json';

```

#### Export diffs to csv:
```sql
\copy (
  SELECT * FROM decision_diff_exp_mp ORDER BY line_no
) TO 'decision_diff_exp_mp.csv' WITH (FORMAT csv, HEADER true);
```

#### Decisions diff as view:
```sql
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

```



## 4. Notes on determinism and reproducibility

- Always use `line_no` to preserve and enforce input ordering.
- Always use UTC to compute day/week keys and Monday detection.
- Treat baseline as immutable; experiments should use separate tables/files.
- If prime detection logic changes (e.g., cached set vs trial division), results must remain identical.

---

## Appendix A: Terminology

- **Canonical event**: the single authoritative event selected per `id`
- **Duplicate replay**: repeated event with same `id` and identical payload fingerprint
- **Duplicate conflict**: repeated event with same `id` but different payload fingerprint
- **Window state**: persisted counters/sums for day/week limits
- **Decision stream**: decisions table keyed by `line_no` to preserve input order


## Appendix B: Outputs produced

- `output.txt` — baseline scenario reference output
- `output_exp_mp.txt` — experimental scenario (Monday multiplier + prime gate)
- `decision_diff_exp_mp.json` — optional diff fixture for tests
