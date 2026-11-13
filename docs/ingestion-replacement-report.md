# Replacing Informatica's ingestion side: preliminary report

**Status: preliminary.** The facts in §1 were extracted from the export on 2026-11-13 with lxml, and
each one cites the XML element it comes from. §2–§6 are **design**: nothing in them has been run. The
full analysis, a small build and validation, come from running
[`prompts/ingestion-hayden-prompt.md`](../prompts/ingestion-hayden-prompt.md) through the Hayden
workflow. §7 describes what that run will produce.

## The problem in one paragraph

PowerCenter does two jobs: it **moves data** (sessions read source tables over database connections
and write target tables, on a workflow schedule), and it **transforms data** (mappings). `pwc2dbt`
replaces the second job only. dbt cannot fetch data from anywhere: it runs SQL on data already in
the warehouse. So when Informatica is switched off, three things must be replaced:
- something that lands the source tables where dbt's `source()` can see them;
- something that runs the steps in the right order;
- anything the workflow did outside mappings.

## 1. What the export tells us about ingestion today (verified)

| Fact | Evidence in the XML |
|---|---|
| **All sources are tables in one Oracle database** (`XEPDB1`), schema `RAW`: RAW_CUSTOMERS, RAW_ORDERS, RAW_ITEMS, RAW_PRODUCTS, RAW_STORES, RAW_SUPPLIES. The repository codepage is MS1252. | `SOURCE DATABASETYPE="Oracle" DBDNAME="XEPDB1_Oracle_32_RAW" OWNERNAME="RAW"`, `REPOSITORY CODEPAGE` |
| **Targets are in the same database** (STG and MART schemas), and later mappings read STG back. | `SOURCE OWNERNAME="STG"`, target instances |
| **Three connections:** `RAW_Jaffleshop`, `STG_Jaffleshop`, `MART_Jaffleshop`. Every writer uses `MART_Jaffleshop`. | `SESSIONEXTENSION/CONNECTIONREFERENCE CONNECTIONNAME` |
| **Keys are declared on the RAW tables:** primary keys `RAW_CUSTOMERS.ID`, `RAW_ORDERS.ID`, `RAW_ITEMS.ID`, `RAW_PRODUCTS.SKU`, `RAW_STORES.ID`; foreign keys `RAW_ORDERS.CUSTOMER → RAW_CUSTOMERS`, `RAW_ITEMS.ORDER_ID → RAW_ORDERS`, `RAW_ITEMS.SKU → RAW_PRODUCTS`, `RAW_SUPPLIES.SKU → RAW_PRODUCTS`. `RAW_SUPPLIES` has **no** primary key. In the seed data, 10 of its 29 IDs repeat across SKUs, and `(ID, SKU)` is unique on all 65 rows. | `SOURCEFIELD KEYTYPE`, `REFERENCEDTABLE` |
| **Full read every run.** There is no source filter, no SQL override, and no incremental parameter. The only parameter is `$$RUN_DATE`, used in a recency calculation, not for filtering. | Source Qualifier `TABLEATTRIBUTE`s empty, a single `MAPPINGVARIABLE` |
| **Full refresh writes:** Pre SQL `TRUNCATE TABLE STG.<t>` on the staging targets, target load type `Bulk`, insert only, commit interval 10,000. | Target instance `Pre SQL`, session `ATTRIBUTE`s |
| **The schedule is on demand.** There is no time-based schedule in the export. | `SCHEDULEINFO SCHEDULETYPE="ONDEMAND"` |
| **Order:** Start → staging → core marts → enriched marts → Command task. | `WORKFLOWLINK`s |
| **A hidden dependency:** the Command task runs `sqlplus` with a `.sql` file that creates reporting views. The file is **not in the export**, and the command line contains a plaintext DB password, which is not reproduced here. | `TASK TYPE="Command"`, `VALUEPAIR` |
| **Demo volume:** about 154k rows in total (largest: items 90,900, orders 61,948). This is the jaffle-shop seed data, not production. | Seed CSVs used in `validation/` |

**What the export cannot tell us** (each item becomes a client question in §6):
- how the RAW tables are populated today: an upstream app, files, another tool;
- production volumes and growth;
- whether source rows change after insert;
- the latency the business needs;
- what the reporting views contain;
- who consumes them.

## 2. Options (design)

| | A. dbt on the same Oracle database | B. Replicate sources into a warehouse | C. Change data capture (CDC) |
|---|---|---|---|
| **What it is** | Point dbt (the dbt-oracle adapter) at the existing database. `source()` reads `RAW.*`, and models write where STG and MART are. | An EL step copies the RAW tables into the warehouse's `raw` schema: a managed connector, open-source EL, or a small script. | Read the database redo log and apply inserts, updates and deletes downstream. |
| **Data movement** | None. | Full copy each run, or incremental. | Continuous stream of changes. |
| **Fits today's pattern** | Exactly: same tables, same full refresh. | Full refresh matches today's truncate-and-reload. | Goes beyond today's needs. |
| **Main risk** | Oracle compute does the dbt work; a dbt-oracle maturity check is needed. | Incremental needs a change column, and most RAW tables have none (see §3). | Operational weight: log access, schema drift, more moving parts. |
| **When to choose** | The goal is "retire Informatica", and the warehouse stays Oracle. | The goal includes moving to a cloud warehouse. | Large volumes, rows updated in place, or near-real-time SLAs. |

**Preliminary recommendation**, to be confirmed by the Hayden run:
- **Short term, A.** It removes Informatica with zero new data movement, because the sources and
  targets already share one database.
- **If a warehouse move is planned, B with full refresh.** It mirrors today's truncate-and-reload
  exactly and is trivial at demo volume.
- **Incremental or CDC only on evidence.** Move to incremental (B) or CDC (C) for a table only once
  production volumes or update patterns show full refresh is too slow.

## 3. Full refresh vs incremental (design)

- Today every run truncates and reloads, so full refresh is a like-for-like replacement and the
  default.
- Incremental extraction needs a reliable "changed since" column. Only `RAW_ORDERS.ORDERED_AT` and
  `RAW_STORES.OPENED_AT` are timestamps, and both are **event** times, not change times. An order
  edited after it was placed would be missed. So incremental means CDC, or adding an
  `updated_at` upstream: a client question.
- Hard deletes in the source are invisible to timestamp-based incremental loads. Full refresh and
  CDC both handle them.

## 4. How it maps to dbt (design)

The converter already writes `output/models/sources.yml`, with one source per owner and its tables.
The ingestion contract adds:
- **Tests from the export's keys:**
  - `unique` + `not_null` on each PRIMARY KEY;
  - `relationships` for each FOREIGN KEY, for example `raw_items.order_id → raw_orders.id`;
  - `RAW_SUPPLIES` gets no uniqueness test, because the export declares no key for it.
- **Freshness:** `loaded_at_field` set to an ingestion timestamp column the EL step adds, plus
  `freshness: warn_after / error_after` thresholds agreed with the client. Source event columns are
  not ingestion times.
- **Descriptions** naming the source database, schema and Informatica connection, for lineage.
- **Generated, not hand-written.** The keys and types are in the XML, so the converter can emit all
  of this. The prompt's build step proposes exactly that, test-first.

## 5. What replaces the workflow (design)

| PowerCenter today | Replacement |
|---|---|
| Workflow `wf_FL_JAFFLESHOP_DEMO`, on demand | An orchestrator job: dbt Cloud job, Airflow, Dagster, or a scheduled CI job. The trigger is on demand, or a schedule once the client states an SLA. |
| Start → `s_STAGING_FULL` | EL step, if option B or C; nothing for option A. |
| `s_STAGING_FULL` → `s_MARTS_CORE` → `s_MARTS_ENRICHED` | One `dbt build`. The dbt DAG already orders the models from their `ref()`s; it does not need the session order. |
| Commit interval, bulk load, Pre SQL truncate | `materialized: table` (create-and-swap), or incremental models later. |
| Command task: `sqlplus` creating the reporting views | Rewrite the views as dbt `view` models once the `.sql` file is obtained. Until then, a post-`dbt build` step that runs it, with the credential taken from a secret store, not a command line. |
| Session logs, reject rows | dbt run results, test failures, source freshness, and orchestrator alerts. |

## 6. Cutover and the questions for the client (design)

**Parallel run.**
- For an agreed period, run PowerCenter and the new pipeline on the same day's source data.
- For every target, compare:
  - the row count;
  - the column-wise checksum, i.e. sum of numerics and hash of keys;
  - an `EXCEPT ALL` diff, which `validation/compare.py` already does against the reference.
- Any difference is investigated and explained before PowerCenter is switched off.
- **Acceptance:** zero unexplained differences for N consecutive runs.
- **Rollback:** PowerCenter stays installed and scheduled-off until acceptance.

**Questions:**
1. How are the RAW tables populated, and by whom? This decides whether any extraction is needed at
   all.
2. Is the warehouse staying Oracle? This decides between option A and option B.
3. What are the production row counts and daily growth per table? This decides full refresh vs
   incremental.
4. Are source rows updated or deleted after insert? If they are, incremental needs CDC.
5. What latency does the business need: daily, hourly, near real time? This affects the schedule
   and the case for CDC.
6. What is in `Jaffleshop_RPT views.sql`, and who uses those views? It is a hidden dependency.
7. Who owns credentials and the secret store for the new jobs? The Command task currently
   embeds a password in plain text.

## 7. What the full Hayden run's report will give us

Running [`prompts/ingestion-hayden-prompt.md`](../prompts/ingestion-hayden-prompt.md) produces
`docs/ingestion-design.md` plus evidence. It will give:

1. **A complete, cited inventory** of everything ingestion-related in the export: sources, keys,
   connections, load options, schedule and hidden tasks. Each item is marked verified or
   unverified.
2. **An explicit list of unknowns**, turned into client questions, each with why it matters and a
   default if unanswered.
3. **A recommendation with rejected alternatives.** The planner compares A, B and C on fit, cost,
   operational burden and failure modes, then attacks its own choice.
4. **A dbt mapping:** the exact `sources.yml` contract (tests, freshness and descriptions). If the
   build step runs, it is generated by the converter and proven by `dbt build` on the validation
   data, including any key the data violates.
5. **An orchestration replacement**, step by step, for the PowerCenter workflow, including the
   reporting-views task.
6. **A cutover and reconciliation plan** with acceptance criteria and rollback.
7. **Effort and risk:** rough sizing, and the top risks with mitigations.
8. **The audit trail:** the briefs in `prompts/subagent-briefs.md`, the plan, the build evidence,
   and any agent mistakes caught, as in the converter work.

The value for a migration decision:
- It says whether replacing Informatica needs **new ingestion at all**, which it doesn't for option
  A.
- It says what that ingestion must guarantee, and how we would prove the new pipeline produces the
  same tables before switching the old one off.
