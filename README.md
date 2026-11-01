# pwc2dbt: Informatica PowerCenter mappings to dbt models

`pwc2dbt` reads a PowerCenter repository export (XML) and writes a dbt project in the DuckDB dialect.
It produces one model per mapping target, built from the sources, transformations, ports,
expressions and connectors in the XML. Nothing about the sample (Jaffle Shop) is hard-coded, and a
test checks that.

When the converter cannot reproduce a column with the same meaning, it does not guess. The column is
left out, the model is marked **INCOMPLETE** and written to `incomplete/` (outside the dbt model
path), and the CLI exits with code 2.

The full README, with feature tables, validation results and per-assumption doc quotes, is
[`docs/README-full.md`](docs/README-full.md).

## How it works

```
XML -> parser.py -> ir.py (IR + graph validation) -> compiler.py (+ expressions.py) -> sql.py -> dbt_project.py
```

- **Connectors are the truth.** A target column's meaning comes from the connector that feeds it,
  never from port names. The sample deliberately swaps dollars and cents into each other's columns.
  The converter keeps that wiring and warns about it.
- **Each target is compiled by walking its lineage backwards**, into one CTE per transformation
  instance. Each CTE holds only the ports some target column needs, plus whatever decides which rows
  survive: filter and router conditions, join keys, group-by ports. A shared upstream is one CTE.
- **Expressions are parsed, not substituted.** A lexer and recursive-descent parser build a tree, and
  a translator with a fixed function whitelist turns it into SQL. Anything else is refused.
- **Cross-mapping links:** a source whose name matches a target in the same export becomes
  `ref()`; every other source becomes `source()`. The dbt DAG follows the data, not the session
  order. The enriched mapping reads only staging tables.

**Supported:** Source Qualifier, Expression (including pure local variables), Filter, Router
(including the default group), Sorter, Joiner (Normal, Master/Detail/Full Outer), Aggregator and
Union. Functions: IIF, ISNULL, UPPER, LTRIM, RTRIM, TRUNC, TO_CHAR, TO_DATE, DATE_DIFF, SUM, MIN,
MAX, AVG and COUNT.

## How to run it

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output output --all-mappings --overwrite
.venv/bin/python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output out_staging --mapping m_FL_JS_STAGING_FULL
.venv/bin/pytest -q                       # 95 tests; they execute the generated SQL in DuckDB
.venv/bin/pip install 'dbt-core==1.12.6' 'dbt-duckdb==1.11.1'
validation/run_validation.sh              # builds jaffle-shop + generated project, then compares them
```

- **Exit codes:**
  - `0`: all models are complete;
  - `2`: output written, but some models are INCOMPLETE;
  - `1`: fatal (bad XML, an invalid graph, an unknown mapping, or a non-empty output directory
    without `--overwrite`).
- **On the sample:** 14 models are complete and 2 incomplete, and the CLI exits 2. The generated
  project is committed in [`output/`](output/), and a test regenerates it byte for byte. For a small
  real example, see `output/models/m_fl_js_staging_full/stg_order_items.sql`.

**Validation.** The demo repo says the XML reimplements
[dbt-labs/jaffle-shop](https://github.com/dbt-labs/jaffle-shop), so that project is the reference.
The older `jaffle_shop_duckdb` has a different schema.
- **dbt checks:** `debug`, `parse` and `compile` succeed, and `build` gives PASS=14, ERROR=0 on about
  154k seed rows.
- **Matching models:** 5 models match the reference on every shared column.
- **Every other difference** traces back to the mapping, not the converter, and was checked by query:
  - dollars and cents are swapped in the XML;
  - `is_perishable` tests for `'Y'`/`'1'` against boolean data;
  - all stores fall under one region threshold.
- **Not proven:** this shows the SQL runs and matches the mapping's logic on this data. It does not
  prove equivalence with a real PowerCenter run, because there is no PowerCenter output to compare.

## What I assumed, and what I chose not to handle

**Assumptions.** [U] means not confirmed by the Informatica docs; every other assumption is
doc-backed, with quotes in the full README.
- Sessions run with high precision off, so decimals compute as doubles. Values are cast only at the
  Source Qualifier and at the target. Oracle `number` becomes double, and `number(p,s)` becomes
  `decimal(p,s)`.
- PowerCenter's NULL rules:
  - Comparisons with NULL return NULL, assuming the server default "Treat Null In Comparison
    Operators As = NULL".
  - IIF with a NULL condition returns the else value.
  - NULL join keys never match.
  - A NULL filter or router condition counts as FALSE.
- Variable ports evaluate top to bottom, so a variable reads only earlier variables for the current
  row.
- An `ERROR()` default on an input port skips rows whose input is NULL. The generated SQL emits
  `where <input> is not null` and a warning.
- `$$RUN_DATE` uses its export default, which you can override with `--vars '{run_date: "..."}'`.
- Unconnected target columns receive NULL [U]. None occur in the sample.

**Not handled.** Each of these fails loudly, as INCOMPLETE or a fatal error, never silently.
- **Transformations:** Lookup, Sequence, Update Strategy, Normalizer, Rank, mapplets, and Custom
  transformations other than Union.
- **Source Qualifier options:** SQL override, user-defined join, and source filter. They are native
  Oracle SQL, which the converter does not translate.
- **Stateful variables:** a variable that reads its own previous-row value, such as
  `v_ORDER_SEQUENCE`. I chose not to rewrite it as `row_number()`, because the mapping's real
  behaviour is a counter across *all* rows.
- **Functions outside the whitelist**, such as DECODE, NVL, SUBSTR and ADD_TO_DATE.
- **Workflow features:** parameter files, sessions, workflows, Command tasks, and incremental loads.

## Where the converter is most likely to produce wrong output

1. **Server settings it cannot see.** NULL-comparison mode, high precision, and the null handling of
   aggregate functions are all assumed to be the defaults.
2. **Per-row errors.** PowerCenter skips a row on divide by zero or a bad date. DuckDB returns NULL
   for the first and fails the whole query for the second. Every division in the sample is guarded.
3. **Faithful but surprising logic.** The converter reproduces the mapping exactly, including:
   - the swapped money columns;
   - `unique_customers` = 0, from variables declared in an order their author probably didn't intend;
   - customers with no orders dropped by a master outer join.
4. **Cross-mapping refs by name only.** Same-named tables in different databases would be linked
   wrongly.
5. **`TO_DATE` with `YY`:** DuckDB maps 69–99 to the 1900s, while PowerCenter uses the current
   century. It is identical for 2000–2068, and the converter prints a warning.

## Where a coding agent got something wrong, and how I caught it

1. **A misread recon.** The recon said `CUSTOMER_360` had 9 unconnected columns. It had truncated a
   dump line at 400 characters. The planner caught it by re-probing the XML: 19 of 19 columns are
   connected.
2. **A pure expression called stateful.** The recon also marked `EXP_MARK_FIRST_CUSTOMER` as
   stateful. The planner applied the documented variable order and showed it is pure.
3. **A hollow test.** A Joiner test stayed green after the master and detail sides were swapped,
   because both key columns had the same name. Mutation testing caught it, and the test was fixed.
4. **A false difference.** The comparison script normalized booleans and decimals differently. It
   was caught by reading the rows behind the count.
5. **The wrong conclusion from a doc excerpt.** From a search excerpt, the orchestrator concluded
   that the `ERROR()` defaults on two ports were harmless, and kept those rows.
   - It was caught when the full docs, read by a browser-enabled model, said input/output defaults
     handle NULL input.
   - A survey of the export then showed those 2 were the only non-blank defaults out of 188.
   - It was fixed test-first: the test failed on the old code before the fix.

## The prompts I gave the coding agent

- [`prompts/initial-prompt.md`](prompts/initial-prompt.md): the initial master prompt, verbatim.
- [`.claude/commands/hayden.md`](.claude/commands/hayden.md): the orchestration skill I supplied.
- [`prompts/subagent-briefs.md`](prompts/subagent-briefs.md): the briefs the orchestrator wrote
  for the planner and the builder, including the defect-fix loop.
- [`prompts/external-consult-informatica-docs.md`](prompts/external-consult-informatica-docs.md):
  the prompt used to have a browser-enabled model read the Informatica docs, which the sandbox
  could not reach.
- The full sequence of my follow-up prompts is listed in
  [`docs/README-full.md`](docs/README-full.md#agent-workflow).
- [`prompts/agent-conversation.md`](prompts/agent-conversation.md): the full agent conversation, rendered
  from the session transcript (user messages, replies, tool calls, subagent reports).

## The process I followed with the agent

I used the Hayden orchestration in `.claude/commands/hayden.md`, with three roles run strictly in
sequence:

1. **Hayden (orchestrator) did the recon.** It read the XML, both Jaffle Shop projects and the
   PowerCenter docs, then wrote [`docs/00-recon.md`](docs/00-recon.md). It covered the facts, the
   traps (cross-wired connectors, a Union hidden as a Custom transformation, stateful variables) and
   the locked decisions.
2. **Hashi (planner) planned and attacked the plan.** As a separate subagent, it re-verified the
   recon, weighed three architectures, and attacked its own plan:
   [`docs/01-plan.md`](docs/01-plan.md), verdict "ready with risks". It caught recon mistakes 1 and 2.
3. **Ahsoka (builder) built and reviewed.** As a separate subagent:
   - it wrote the end-to-end proof test first and saw it fail;
   - it built the converter step by step;
   - it reviewed its own diff and mutation-tested it ([`docs/02-build.md`](docs/02-build.md)).
4. **Hayden closed.** It re-ran the tests, CLI, regeneration and dbt validation in a fresh shell,
   read the diff, and committed.
5. **One defect loop.** When the docs contradicted an assumption (mistake 5), Hayden sent Ahsoka a
   defect-only brief, then verified the fix the same way.
