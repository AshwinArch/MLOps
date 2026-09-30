# ADR-004: Relational, time-partitioned metric storage first

## Status
Accepted

## Context
Sample data is daily per model/version; real systems emit per-minute per model across plants. The assignment needs persistence, filtering and comparison, not a TSDB.

## Decision
Store metrics in `metric_points` with a unique key `(model, version, env, ts)` and an index on `(model, ts)`; expose a read model with health classification. Plan range-partitioning and rollups before introducing a TSDB.

## Alternatives Considered
- Prometheus/Timescale/ClickHouse from day one: best at scale, but extra infra and skills for little benefit at current volume.
- Computing metrics from logs: fragile, slow queries.

## Consequences
### Positive
One datastore, transactional seed/tests, simple joins to registry data.
### Negative
Needs partitioning/retention discipline; ad-hoc wide queries will not scale past ~10^8 rows.

## Follow-up Actions
Ingestion API/consumer, rollup jobs, retention policy, migrate hot path to ClickHouse/Timescale when p95 dashboard latency exceeds budget.
