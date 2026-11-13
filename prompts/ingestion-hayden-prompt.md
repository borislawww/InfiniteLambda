# Prompt: replace Informatica's ingestion side so dbt has its sources

Run it with the Hayden command: `/hayden <paste everything below the line>`.

---

## Context

This repo has `pwc2dbt`, which converts Informatica PowerCenter mappings (XML) into dbt models.

PowerCenter does two jobs, and the converter replaces only the second:
1. **Extract and load.** Its sessions read the source tables through relational connections and
   write to target tables, on a workflow schedule.
2. **Transform.** Mappings: Source Qualifier, Expression, Joiner, and so on.

If Informatica is switched off, nothing reads the sources, nothing lands them where dbt can see
them, and nothing runs the steps in order. dbt only transforms data that is already in the
warehouse, so we need a design for getting source data in and wiring it to the dbt `source()`
definitions.

Inputs, all in this repo:
- The export: `tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml`.
- The converter: `src/pwc2dbt/`, especially `dbt_project.py`, which writes `models/sources.yml`.
- Generated output: `output/models/sources.yml`.
- Validation: `validation/run_validation.sh` currently fakes ingestion by loading jaffle-shop seeds
  into DuckDB `raw.*`.
- Earlier recon: `docs/00-recon.md`, and `docs/ingestion-replacement-report.md`, a preliminary
  analysis to verify, not to trust.

## Task

1. **Recon (Hayden).** Inventory everything in the export that describes ingestion:
   - source definitions (database type, DBD name, owner/schema, columns, types, nullability,
     PRIMARY/FOREIGN KEY with referenced table);
   - session connections (`CONNECTIONREFERENCE`: reader and writer, connection names);
   - load options (Pre SQL, truncate, target load type, commit interval, treat source rows as);
   - source filters and SQL overrides, mapping parameters and variables (incremental hints);
   - workflow order and schedule, and every non-session task (Command tasks and what they call).
   Mark each fact verified or unverified, as in `docs/00-recon.md`. State plainly what the export
   *cannot* tell us, such as how the RAW tables are populated upstream, data volumes, change rates
   and latency SLAs, and turn each gap into a question for the client.
2. **Plan (Hashi).** Compare at least three ingestion approaches and pick the smallest complete one
   for this export, with reasons:
   - (A) keep the data where it is and run dbt on the same database (dbt-oracle on the
     RAW/STG/MART schemas);
   - (B) replicate the source tables into a warehouse with an EL tool or library (managed
     connector, open-source EL, or a small script), full refresh vs incremental;
   - (C) change data capture from the source database's log.

   For each, cover:
   - how it maps to dbt `source()`, and what goes in `sources.yml`: `loaded_at_field`, freshness,
     and tests derived from the export's keys (unique, not_null, relationships);
   - full refresh vs incremental, given that most RAW tables have no change-timestamp column;
   - what replaces the workflow: orchestration order EL → `dbt build` → reporting views, plus
     retries, alerting and backfill;
   - how the Command task's reporting views move into dbt, or why they can't yet;
   - cost, operational burden and failure modes;
   - the cutover plan: parallel run of PowerCenter and dbt on the same day's data, with row-count
     and checksum reconciliation per target, and rollback.

   Attack the plan as usual.
3. **Build (Ahsoka), only if Hashi judges it worth its code.** Make the smallest change that turns
   metadata the export already has into ingestion contracts:
   - extend the converter's `sources.yml` with column lists and types, `unique` + `not_null` tests
     on PRIMARY KEY columns, `relationships` tests for FOREIGN KEYs, and a description naming the
     source database, owner and Informatica connection;
   - derive all of it from the XML, test-first, with no names hard-coded;
   - re-run `pytest`, regenerate `output/`, and run `dbt build` in `validation/`, so the new source
     tests run against the seed data. Report any test the real data fails, and do not weaken it.
4. **Report (Hayden).** Write `docs/ingestion-design.md` for a reader who knows neither tool. It
   must answer:
   - **What we found:** the source systems, tables, keys, load pattern, schedule and hidden
     dependencies (the reporting-views Command task).
   - **What we recommend:** the approach, with a one-paragraph why, the rejected options and their
     reasons, and the tooling categories. No vendor lock-in unless the facts force it.
   - **How it maps to dbt:** `sources.yml` and its tests, the freshness rules, and how the generated
     models connect.
   - **What runs when:** the orchestration steps that replace the PowerCenter workflow.
   - **How we prove it:** the parallel-run reconciliation, the acceptance criteria, and rollback.
   - **What we don't know yet:** the client questions, each with why it matters and a default if
     unanswered.
   - **Effort and risk:** a rough sizing, and the top risks with mitigations.

## Rules

- Follow `.claude/commands/hayden.md` exactly: Hashi plans and attacks, Ahsoka is the only one who
  edits code, the two run sequentially, and Hayden closes in a fresh shell.
- Every claim about the export cites the XML element or attribute it came from. Every claim about
  PowerCenter behaviour cites docs.informatica.com, or is marked unverified.
- No credentials in the repo or the report. The export's Command task contains a plaintext DB
  password: never reproduce it.
- Do not claim an approach works unless something in this session ran it. Design statements are
  labelled as design.
- Save each subagent brief to `prompts/subagent-briefs.md`, and record real mistakes in the README
  section on agent mistakes.
