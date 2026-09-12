# Data Model — DynamoDB Single Table

Written before any application code, per the plan's order of work. This document is the
design; `app/graphql-api/internal/store` is the implementation of it.

Table name: `linkpulse` · Billing mode: **PROVISIONED, 20 RCU / 20 WCU on the table and
5 / 5 on GSI1 — 25 / 25 in total, which is the whole always-free allowance** (never on-demand
— on-demand has no free tier, and provisioned capacity is structurally incapable of exceeding
the ceiling I set).

---

## 1. Access Patterns

Everything the application does, enumerated before choosing keys. If a pattern is not on
this list, the table is not designed for it, and I say so rather than bolting on a scan.

| # | Pattern | Frequency | Consistency |
|---|---|---|---|
| A1 | Resolve short code to long URL (the redirect hot path) | **Very high read** | Eventually consistent is fine |
| A2 | Create a link | Low write | Must be atomic (no code collisions) |
| A3 | Record a click | **Very high write** | Fire-and-forget, must not block the redirect |
| A4 | Total click count for a link | Medium read | Eventually consistent |
| A5 | Clicks per day for a link, over a date range | Medium read | Eventually consistent |
| A6 | Recent click feed for a link (last N clicks, newest first) | Medium read | Eventually consistent |
| A7 | List links owned by a user, newest first | Low read | Eventually consistent |

A6 is why the click feed **polls** rather than using GraphQL subscriptions — the read is a
cheap bounded `Query`, so polling costs less than holding sticky WebSocket connections that
would fight the HPA and the drain story.

---

## 2. Item Types and Key Design

One table, four item types, discriminated by `type`. PK is always an entity prefix, so items
that are read together live together.

### LINK — the link itself (A1, A2)

```
PK    LINK#<code>
SK    META
type  LINK
attrs code, longUrl, ownerId, createdAt, active
```

A1 is a single `GetItem` on `PK=LINK#<code>, SK=META`. That is the design goal for the hot
path: **one round trip, 0.5 RCU eventually consistent, no index, no query.** Nothing about
the analytics design is allowed to make the redirect slower.

A2 is a `PutItem` with `ConditionExpression: attribute_not_exists(PK)`. The condition is
what makes short-code generation safe under concurrency without a lock table — a collision
returns `ConditionalCheckFailedException`, and the app regenerates and retries.

### CLICK — one item per click (A6)

```
PK    LINK#<code>
SK    CLICK#<ts_rfc3339_nanos>#<rand>
type  CLICK
attrs ts, referrer, country, uaClass
TTL   expiresAt (30 days)
```

The sort key is timestamp-prefixed, so A6 is `Query(PK=LINK#<code>, SK begins_with CLICK#,
ScanIndexForward=false, Limit=N)` — newest first, no sort in application code, no index.

The random suffix disambiguates same-instant clicks. TTL keeps the raw feed from growing
without bound; the aggregate below is the durable record, so expiry loses nothing that
matters.

### CLICKSTAT — the daily aggregate (A4, A5)

This is the item the hot-partition problem lives in, and it is the interesting part of the
design.

```
PK    LINK#<code>
SK    STAT#<YYYY-MM-DD>#<shard>
type  CLICKSTAT
attrs count  (ADD 1 via UpdateItem)
```

A5 is `Query(PK=LINK#<code>, SK BETWEEN STAT#<from>#0 AND STAT#<to>#~)`, summing shards per
day. A4 is the same query with no date bound.

---

## 3. The Hot-Partition Problem

**The failure.** A3 writes to `PK=LINK#<code>`. Every click on one link is a write to one
partition key. A DynamoDB partition sustains **1000 WCU / 3000 RCU regardless of what the
table is provisioned for** — so a single viral link throttles at the partition limit even if
the table has idle capacity elsewhere. On my table the ceiling is lower still: 20 WCU total.
This is not a hypothetical I read about. It is the actual failure mode of the schema above,
and my free-tier ceiling is what lets me reach it on purpose (chaos experiment 1).

**Why the naive fixes do not work.** Raising provisioned capacity does not help: the
partition limit is per-partition, not per-table, so a hot key still throttles at 1000 WCU no
matter how large the table's budget is. Application-side batching does not help either —
`BatchWriteItem` is not atomic and cannot express counter increments at all.

**Mitigation: write-sharding.** The aggregate SK carries a shard suffix:

```
SK  STAT#2026-09-10#<shard>     shard = rand(0, CLICK_SHARDS-1)
```

Writes scatter across `CLICK_SHARDS` distinct sort keys. Reads fan back in by summing a
day's shards — a bounded `Query`, since the shard count is known and small.

The shard count is **configuration, not code** (`CLICK_SHARDS`, default 16). That is
deliberate: experiment 1 runs the identical binary at `CLICK_SHARDS=1` to reproduce the
throttle, then at `CLICK_SHARDS=16` to show the graphs flatten. Proving the mitigation by a
config flip under identical load is much stronger evidence than a rewrite that changes two
things at once.

**The trade-off I am choosing.** Sharding moves cost from the write path to the read path:
A4/A5 now read up to `CLICK_SHARDS` items per day instead of one. That is the right
direction for this workload — clicks are very high write and medium read (§1), so I am
paying in the cheaper currency. Past roughly 50 shards the read amplification stops being
free and the answer changes to the alternative below.

**The alternative I rejected, and when I would pick it.** DynamoDB Streams to a Lambda that
folds CLICK items into aggregates asynchronously. Writes then land as CLICK items only
(naturally distributed by the random SK suffix), and read amplification decouples from write
volume entirely. I rejected it because Streams plus Lambda is metered, would have to live
inside the 72-hour burst window, and could not run in CI — which would cost me the
automated, publicly timestamped evidence that experiment 1 exists to produce.
Write-sharding runs on LocalStack in GitHub Actions forever. **If the click rate outgrew
sharding's read amplification, Streams is the next move**, and the aggregate item shape
above does not have to change for it.

---

## 4. GSI1 — Owner's Links (A7)

```
GSI1PK  USER#<ownerId>
GSI1SK  CREATED#<ts_rfc3339>
projection: INCLUDE (longUrl, active)
```

A sparse index: only LINK items carry the GSI1 attributes, so CLICK and CLICKSTAT items —
the overwhelming majority of rows — are **not** projected and cost no index writes. That
sparseness is the whole reason A7 is affordable next to A3.

The projection is two attributes rather than the whole item because an index always
carries the key attributes: `code` is recoverable from `PK`, `createdAt` from `GSI1SK`, and
`ownerId` from the query itself. Only `longUrl` and `active` genuinely have to be copied,
and every projected attribute is a byte written on every link create.

Provisioned 5 RCU / 5 WCU. Index capacity is separate from table capacity and counts against
the same always-free allowance — which is why the table itself is 20/20 and not 25/25. (The
first cut of this document said 25/25 for the table *and* 5/5 for the index, a 30/30 total
that would have billed about $3 a month; `scripts/metered-resources.py` caught it in phase 8.)

---

## 5. Capacity Budget

| Operation | Cost | Notes |
|---|---|---|
| A1 redirect | 0.5 RCU | eventually consistent GetItem, item < 4 KB |
| A2 create | 1 WCU table + 1 WCU GSI1 | conditional put |
| A3 click | 1 WCU (CLICK) + 1 WCU (CLICKSTAT) | 2 WCU per click is the real number |
| A5 range read | 0.5 RCU per 4 KB page | items are tiny, so a page holds many |

**2 WCU per click against 20 WCU provisioned means ~10 clicks/sec is my hard ceiling.** k6
crosses that trivially, which is exactly why the throttle experiment is reproducible on a
free account.

---

## 6. When Single-Table Is the Wrong Choice

I expect to be asked this, so it is written down rather than improvised.

Single-table wins here because my access patterns are known, few, and all rooted at a link.
It is the wrong choice when:

- **Access patterns are unknown or churning.** Single-table design bakes queries into keys;
  every new pattern is a migration or another GSI. An early-stage product still discovering
  its queries is better served by a relational store.
- **You need ad-hoc query, joins, or aggregation.** There is no `GROUP BY`. My daily counts
  work only because I decided to aggregate on write. Anything genuinely analytical belongs
  in a warehouse, fed from here.
- **Items in one collection have wildly different sizes or lifecycles**, which turns a
  Query's 1 MB page limit into a pagination problem.
- **The team cannot read it.** A single-table schema is genuinely harder for the next
  engineer than four normalised tables. That is a real operational cost, and I would not pay
  it for a low-traffic internal service.
- **Strong consistency across entities matters.** Transactions exist, but they are capped at
  100 items and cost double.

For LinkPulse the patterns are fixed, the hot path is a single key lookup, and write volume
is the whole problem — which is the case single-table design is actually for.
