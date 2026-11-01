# pwc2dbt: Informatica PowerCenter mappings to dbt models

`pwc2dbt` reads a PowerCenter repository export (XML) and writes a dbt project with one model per
mapping target. It derives the SQL from the XML: from sources, transformations, ports, expressions
and connectors. Nothing about the sample (Jaffle Shop) is hard-coded; a test greps the converter
source for every mapping, target, source and port name in the fixture.

The SQL dialect is DuckDB. When the converter cannot reproduce a column with the same semantics,
it does not guess. That target column is left out, the model is marked **INCOMPLETE**, it is
written to `incomplete/` (outside the dbt model path), and the CLI exits with code 2.

## Architecture

```
PowerCenter XML -> parser.py -> ir.py (IR + graph validation) -> compiler.py (+ expressions.py) -> sql.py -> dbt_project.py
                                                                                                         cli.py drives it
```

| Module | Responsibility |
|---|---|
| `parser.py` | Safe XML load (Windows-1252, DTD never loaded, entities never resolved), into frozen dataclasses. |
| `ir.py` | The IR (folder, sources, targets, mappings, transformations, ports, instances, connectors, parameters) and `validate_mapping`: unresolved definitions, bad connector endpoints and directions, fan-in, cycles, duplicate ports, unsafe identifiers, a target loaded twice. |
| `expressions.py` | Lexer, recursive-descent parser and translator for the PowerCenter expression language. A small whitelist of functions; anything else raises `Unsupported`. |
| `compiler.py` | Demand-driven compilation of each target: one CTE per transformation instance, built only for the ports the target needs. |
| `sql.py` | Renders a compiled model as dbt SQL. |
| `dbt_project.py` | Writes `dbt_project.yml`, `models/<mapping>/*.sql`, `schema.yml`, `sources.yml` and `incomplete/`. |
| `cli.py` | Arguments, progress and diagnostics, exit codes. |

How a target is compiled:

1. **Connectors are the truth.** A target column's meaning comes only from the connector that feeds
   it, never from port names. The export has deliberately swapped wiring (for example
   `EXP_ORDERS.SUBTOTAL -> STG_ORDERS.SUBTOTAL_CENTS`). The converter keeps it and prints a warning.
2. **Lineage is walked backwards** from each connected target column. A column depends on the ports
   that compute it, and on the row-shaping ports of every transformation it passes through: filter
   and router conditions, join keys, group-by ports, and every connected column under Select Distinct.
3. **One CTE per transformation instance**, in dependency order, selecting only the needed ports.
   Inside a CTE, columns keep that transformation's own port names (PowerCenter makes them unique,
   for example `ORDER_ID` and `ORDER_ID1` in a Joiner). A shared upstream instance is one CTE that
   several consumers read, as in PowerCenter.
4. **A problem only matters if a target needs it.** An untranslatable port that no target column
   reaches is ignored. A needed one removes that column and marks the model INCOMPLETE, naming the
   mapping, transformation, port and reason.
5. **Models that reference an INCOMPLETE model become INCOMPLETE** too, so `dbt parse` never meets a
   `ref()` to a model that is not there.

Cross-mapping dependencies: a source whose name equals a target written in the same export (for
example `STG_ORDERS`) becomes `{{ ref('stg_orders') }}`. Every other source becomes
`{{ source('<owner>', '<table>') }}`.

The workflow `wf_FL_JAFFLESHOP_DEMO` runs the sessions in the order staging, then core marts, then
enriched marts, and the demo repository's README describes the same flow. The data dependencies are
narrower than that order suggests. `m_FL_JS_MARTS_ENRICHED` reads only `STG_*` tables, never a
core-mart target, so the generated enriched models `ref()` staging models only. The DAG follows the
data, not the session order.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'          # lxml at runtime; pytest and duckdb for tests
.venv/bin/pip install 'dbt-core==1.12.6' 'dbt-duckdb==1.11.1'   # only for validation/
```

## Run the converter

```bash
.venv/bin/python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output output --all-mappings --overwrite
.venv/bin/python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output out_staging --mapping m_FL_JS_STAGING_FULL
```

- Exit codes:
  - `0`: every model is complete;
  - `2`: output written, but at least one model is INCOMPLETE;
  - `1`: fatal, meaning unreadable XML, an invalid mapping graph, an unknown mapping, or a non-empty
    output directory without `--overwrite`. When both 1 and 2 apply, 1 wins.
- `--overwrite` deletes only `models/` and `incomplete/` in the output directory before writing, so
  a model that was complete in an earlier run cannot survive as a stale file.
- On the sample the converter prints 14 COMPLETE and 2 INCOMPLETE models and exits with 2.
  `ORDERS.CUSTOMER_ORDER_NUMBER` and `ORDER_FINANCIALS.CUSTOMER_ORDER_NUMBER` come from the stateful
  variable `EXP_CUSTOMER_ORDER_NUM.v_ORDER_SEQUENCE`.
- The generated project is committed in `output/`. A test regenerates it and compares it byte for
  byte.

## Run the tests

```bash
.venv/bin/pytest -q                 # 95 tests, about 2 s
validation/run_validation.sh        # needs network: clones jaffle-shop, runs dbt, compares (about 1 min)
```

| Test file | Covers |
|---|---|
| `test_parser.py` | Real-export counts (3 mappings, 12 sources, 16 targets, 1,066 connectors), ports, groups, field dependencies, instance-to-definition resolution, mapping-scoped names, Windows-1252, XXE, malformed and ambiguous exports. |
| `test_graph.py` | Linear pipeline, a shared upstream used by both sides of a self-join, chained joiners, two instances of one source, multiple targets, cross-mapping `ref()`, cycles, bad references, fan-in, direction, unsafe names, unsupported types, propagation of incompleteness and of failed mappings, a target loaded by two mappings. |
| `test_expressions.py` | Precedence, nested IIF, CRLF, NULL handling, DATE_DIFF, TRUNC, TO_CHAR and TO_DATE executed in DuckDB, 19 rejected constructs, aggregate rules. |
| `test_transformations.py` | One fixture per transformation type, executed in DuckDB: Source Qualifier (plus Distinct and Source Filter), Expression (pass-through, variable inlining, stateful), Filter, Router (a row in two groups, and the default group), Sorter, Joiner (all four types, both condition orders, NULL keys), Aggregator, Union (field dependencies beat port order), target casts, unconnected target columns, input default values (an `ERROR()` default drops NULL-input rows in each single-upstream type; Joiner, Union, `ABORT()` and unconnected cases are INCOMPLETE). |
| `test_integration.py` | The real XML end to end: exit code 2, the exact model split, all 14 complete models executed on hand-made rows with semantic assertions, committed output equals regeneration, CLI exit codes, refusal to overwrite, no hard-coded names. |

Tests assert values that DuckDB computed by running the generated SQL, not keywords in the text.

## Example output

This is `output/models/m_fl_js_staging_full/stg_supplies.sql`, copied from the generated file:

```sql
-- Generated by pwc2dbt from mapping m_FL_JS_STAGING_FULL, target instance STG_SUPPLIES. Do not edit.

with

sq_raw_products1 as (

    select
        cast(raw_products1.sku as varchar) as sku
    from {{ source('raw', 'raw_products') }} as raw_products1

),

sq_raw_supplies as (

    select
        cast(raw_supplies.id as varchar) as id,
        cast(raw_supplies.name as varchar) as name,
        cast(raw_supplies.cost as double) as cost,
        cast(raw_supplies.perishable as varchar) as perishable,
        cast(raw_supplies.sku as varchar) as sku
    from {{ source('raw', 'raw_supplies') }} as raw_supplies

),

jnr_supplies_products as (

    select
        detail.id as id,
        detail.name as name,
        detail.cost as cost,
        detail.perishable as perishable,
        detail.sku as sku
    from sq_raw_supplies as detail
    inner join sq_raw_products1 as master
        on master.sku = detail.sku

),

exp_supplies as (

    select
        jnr_supplies_products.id as supply_id,
        jnr_supplies_products.name as supply_name,
        jnr_supplies_products.cost as supply_cost_cents,
        jnr_supplies_products.cost / 100 as supply_cost,
        case when jnr_supplies_products.perishable = 'Y' or jnr_supplies_products.perishable = '1' then 1 else 0 end as is_perishable,
        jnr_supplies_products.sku as product_id
    from jnr_supplies_products

)

select
    cast(exp_supplies.supply_id as varchar) as supply_id,
    cast(exp_supplies.supply_name as varchar) as supply_name,
    cast(exp_supplies.supply_cost_cents as decimal(12,2)) as supply_cost_cents,
    cast(exp_supplies.supply_cost as decimal(12,2)) as supply_cost,
    cast(exp_supplies.is_perishable as decimal(1,0)) as is_perishable,
    cast(exp_supplies.product_id as varchar) as product_id
from exp_supplies
```

PowerCenter facts it shows:

- **Joiner, Normal Join.** `SKU1` is the master port (`INPUT/OUTPUT/MASTER`), so `SQ_RAW_PRODUCTS1` is
  the master side. The condition is written `SKU1 = SKU`; the side comes from the port type, not from
  the condition order.
- **Pruning.** Only `sku` is read from the second `RAW_PRODUCTS` instance, because that is all the
  join needs.
- **IIF translation.** `IIF(PERISHABLE = 'Y' OR PERISHABLE = '1', 1, 0)` is translated literally.
- **Casts at the two type boundaries.** At the Source Qualifier, the cast is to the port type
  (decimals are doubles because the sessions run with high precision off). At the target, the cast
  is to the target column type.

## Supported PowerCenter features

| Feature | Translation |
|---|---|
| Source Qualifier | `select cast(col as <port type>) ... from source()/ref()`; `Select Distinct=YES` gives `select distinct` over every connected port |
| Expression | Output ports translated; I/O ports passed through; pure local variables inlined in parentheses, in port order |
| Filter | `where <condition>` |
| Router | One CTE per needed group with `where <condition>`. The default group gets `where not coalesce(c1, false) and not coalesce(c2, false) ...`. A row can land in several groups. |
| Sorter | Transparent, so no `ORDER BY` (dbt tables have no row order). A Sorter with an input default value gets its own CTE, so the default rules below apply to it. |
| Joiner | Normal becomes `inner join`. Master Outer becomes `detail left join master`. Detail Outer becomes `master left join detail`. Full Outer becomes `full outer join`. Conditions are conjunctions of `port = port`. |
| Aggregator | `group by` the GROUPBY ports, with SUM, MIN, MAX, AVG, COUNT(port), COUNT(*), and expressions inside and around them |
| Union (Custom Transformation `pmuniontrans`) | `union all`, input-to-output mapping taken from `FIELDDEPENDENCY` metadata |
| Functions | IIF (3 arguments), ISNULL, UPPER, LTRIM and RTRIM (1 argument), TRUNC(date), TO_CHAR(date, fmt), TO_DATE(string, fmt), DATE_DIFF(d1, d2, 'D', 'DD' or 'DDD'), plus the aggregates above |
| Operators | `+ - * / ||`, `= <> != < <= > >=`, NOT, AND, OR, unary minus, NULL |
| Format strings | YYYY, YY, MM, DD, HH24, MI, SS, and the separators `- / : . ,` and space |
| Input-port `ERROR()` default | Expression, Filter, Router, Aggregator and Sorter: `where <input> is not null` on the rows entering the transformation, whether or not a target needs the port, with a WARNING |
| Mapping parameters | Date/time only, rendered as `cast('{{ var("run_date", "<export default>") }}' as timestamp)` |

## Unsupported (each one fails loudly as INCOMPLETE or ERROR, never silently)

- **Transformations:** Lookup, Sequence Generator, Update Strategy, Normalizer, Rank, Stored
  Procedure, mapplets, reusable or shortcut transformations, and Custom Transformations other than
  Union.
- **Source Qualifier and Sorter options:** SQL override, User Defined Join and Source Filter (native
  Oracle SQL), and Sorter `Distinct=YES`.
- **Aggregators:** Aggregators without group-by ports, variable ports in an Aggregator, and a non-group-by
  I/O port (PowerCenter returns the last row's value).
- **Stateful local variables:** a variable that reads itself or a later variable is a value carried
  from the previous row. Set-based SQL cannot express it, and the converter will not invent a window
  function for it.
- **Joiners:** conditions other than port equalities, and case-insensitive string comparison.
- **Input-port defaults:** `ERROR()` or `ABORT()` on a Joiner or Union input, `ABORT()` on any
  input (it aborts the session on NULL), `ERROR()` on an unconnected input (every row would be
  skipped), and any other non-NULL constant.
- **Functions and arguments:** DECODE, NVL, SUBSTR, INSTR, CONCAT, ADD_TO_DATE and every other
  function not listed above; TO_CHAR on numbers; TRUNC with a format; LTRIM and RTRIM with a trim
  set; two-argument IIF; implicit type conversions such as comparing a string with a number.
- **Expression syntax:** comments inside expressions, and string literals that contain Jinja
  delimiters.
- **Parameters, workflows and load options:** mapping variables, non-date mapping parameters,
  parameter files, sessions, workflows, Command tasks, and incremental loads.

## Assumptions

Each of these is either a printed WARNING or listed here. **[U]** marks a claim that could not be
confirmed from the Informatica documentation. The documentation pages could not be read directly
from the build sandbox (see Agent workflow), so claims were checked against search excerpts of them.

- **Execution model.**
  - PowerCenter sessions run with high precision OFF, so decimal ports compute as doubles. Values
    are cast only at the Source Qualifier and at the target, and intermediate integer and decimal
    port types are not rounded **[U]**.
  - Target types: Oracle `number(p,s)` becomes `decimal(p,s)`, and `number` becomes `double`. This
    matches PowerCenter's own mapping, where Oracle Number maps to Double and Number(P,S) to Decimal
    ([Oracle and Transformation Datatypes](https://docs.informatica.com/data-integration/powercenter/10-5/designer-guide/appendix-a--datatype-reference/oracle-and-transformation-datatypes.html)).
    `date` becomes `timestamp`, because Oracle DATE has a time part.
  - Targets are truncated and reloaded by their sessions, so every model is materialized as `table`.
- **NULL semantics.**
  - Comparisons with NULL return NULL. This assumes the Integration Service default "Treat Null In
    Comparison Operators As = NULL"; under HIGH or LOW, comparisons with NULL change and the SQL
    would differ.
  - IIF with a NULL condition returns the else value, like SQL `CASE`. The doc's example table
    shows this (`SALES = NULL` takes value2,
    [IIF](https://docs.informatica.com/data-integration/powercenter/10-5/transformation-language-reference/functions/iif.html)).
  - Joiner keys that are NULL never match, even when both sides are NULL
    ([Defining a Join Condition](https://docs.informatica.com/data-integration/powercenter/10-4-0/transformation-guide/joiner-transformation/defining-a-join-condition.html)).
  - A filter or router condition that is NULL counts as FALSE, so a router row goes to the default
    group.
  - Aggregates ignore NULLs.
- **Dates.**
  - `DATE_DIFF(d1, d2, 'DD')` returns fractional days, d1 minus d2.
  - `TO_DATE` with `YY`: DuckDB maps 69 to 99 to the 1900s, while PowerCenter uses the current century.
    This is a WARNING; results are identical for the years 2000 to 2068.
- **Defaults and parameters.**
  - `ERROR(...)` as the default of an INPUT or INPUT/OUTPUT port skips every row whose input on
    that port is NULL. The generated SQL drops those rows (`where <input> is not null`) and prints a
    WARNING per port.
    - "For input/output ports, the Integration Service uses default values to handle null input
      values" ([General Rules for Default Values](https://docs.informatica.com/data-integration/powercenter/10-5/transformation-guide/working-with-transformations/default-values-for-ports/general-rules-for-default-values.html)).
      The system default is NULL and shows as blank.
    - "When you use the ERROR function as a default value, the Integration Service skips the row
      with the null value" ([Skip Null Records](https://docs.informatica.com/data-integration/powercenter/10-5-8/transformation-guide/working-with-transformations/default-values-for-ports/user-defined-default-input-values/skip-null-records.html)).
      Skipped rows are not written to the reject file.
    - In the sample, 186 of 188 Expression I/O ports have a blank default. The two with
      `ERROR('Transformation Error')`, `EXP_LOCATIONS.TAX_RATE` and `EXP_ORDER_ITEMS.ORDER_ID`, are
      user-defined. Their columns hold no NULLs in the seeds, so no row is dropped.
    - Joiner and Union inputs with such a default, an unconnected input with it, and any `ABORT()`
      input default make the model INCOMPLETE.
  - Any other non-NULL input default makes the model INCOMPLETE.
  - Output-port default values apply only to transformation errors, so they are ignored.
  - `$$RUN_DATE` has no parameter file in the export, so its default (`12/31/2025`) is used. Override
    it with `dbt build --vars '{run_date: "..."}'`.
- **Unconnected target columns** receive NULL, with a WARNING and a SQL comment **[U]**. The docs
  say only "The Integration Service ignores unconnected ports"; whether NULL or the database column
  default is written is not stated. None occur in the sample.
- **Cross-mapping refs** are resolved by table name only. A source and a target with the same name in
  different databases would be linked wrongly.
- **Source data types** come from the loaded tables. In the jaffle-shop seeds `perishable` is
  BOOLEAN, which the Source Qualifier casts to `'true'` or `'false'`.

## Where the converter can produce incorrect output

1. **Unverified PowerCenter semantics.** These are the **[U]** items above: unconnected target
   columns and intermediate port precision. Also, NULL comparisons assume the Integration Service
   default "Treat Null In Comparison Operators As = NULL". If PowerCenter behaves differently, the
   SQL is silently different.
2. **Variable port order.** Variables are evaluated in port order, as documented. A mapping that
   relies on the "previous row" idiom but declares the variables in the wrong order (here
   `EXP_MARK_FIRST_CUSTOMER`) is pure, so it is translated literally, and
   `STORE_DAILY_KPIS.UNIQUE_CUSTOMERS` is 0. That faithfully reproduces what the mapping says; it is
   probably not what its author meant.
3. **Numeric precision.** Arithmetic runs in doubles and is rounded only at the target. If a session
   ran with high precision ON, PowerCenter would compute in decimals, and results could differ in the
   last digits.
4. **Errors that PowerCenter raises per row.** PowerCenter rejects or skips rows on division by zero
   and unparsable TO_DATE strings. DuckDB returns NULL for division by zero and
   fails the whole query on a bad date. Every division in the sample is guarded by IIF.
5. **Sorter order for stateful logic.** This is refused today. Any future support would depend on
   the sort order, which the converter currently ignores.

## Validation against Jaffle Shop

**Setup.** `validation/run_validation.sh` does the following:

1. Clones `dbt-labs/jaffle-shop` at commit `5beb145b`. This is the current Jaffle Shop, whose seeds
   match the XML's RAW tables; `jaffle_shop_duckdb` has a different schema. The demo repository's
   own README (`infinitelambda/flowline-pwc-demo`) says the export "reimplements the dbt Jaffle Shop
   (https://github.com/dbt-labs/jaffle-shop)", which confirms this is the reference to use.
2. Makes three edits so it runs on dbt 1.x: it drops `require-dbt-version` and the `dbt-cloud` block,
   and pins `dbt-utils` to git revision 1.3.0.
3. Seeds and builds the reference into schema `main`.
4. Runs the converter and builds the generated project into schema `gen`, in the same DuckDB file.
5. Runs `validation/compare.py`.

**dbt results** (run in this session, dbt-core 1.12.6, dbt-duckdb 1.11.1):

| Step | Result |
|---|---|
| Reference `dbt seed` | PASS=6 |
| Reference `dbt build` | PASS=43, NO-OP=3, ERROR=0 |
| Generated `dbt debug` | OK |
| Generated `dbt parse` | OK |
| Generated `dbt compile` | OK |
| Generated `dbt build` | PASS=14, ERROR=0, on 154k seed rows. No DECIMAL overflow at the target precisions. |

**Comparison.** For each model with a reference of the same name, the comparison covers the shared
columns. "Rows" is `EXCEPT ALL` in both directions, with numerics rounded to 2 dp and booleans as
0 or 1.

| Model | Rows gen / ref | Rows only in gen / only in ref | Shared columns that differ |
|---|---|---|---|
| stg_customers | 935 / 935 | 0 / 0 | none |
| stg_locations | 6 / 6 | 0 / 0 | none (`region` is extra) |
| stg_order_items | 90,900 / 90,900 | 0 / 0 | none |
| stg_supplies | 65 / 65 | 0 / 0 | none on shared columns |
| supplies | 65 / 65 | 0 / 0 | none on shared columns |
| stg_orders | 61,948 / 61,948 | 61,465 / 61,465 | subtotal, tax_paid and order_total, both the dollars and the cents columns |
| stg_products | 10 / 10 | 10 / 10 | product_price |
| products | 10 / 10 | 10 / 10 | product_price |
| order_items | 90,900 / 90,900 | 90,900 / 90,900 | product_price |
| customers | 935 / 935 | 935 / 935 | lifetime_spend, lifetime_spend_pretax, lifetime_tax_paid |
| customer_360, order_item_enriched, product_daily_performance, store_daily_kpis | built, with 935, 90,900, 3,635 and 538 rows | n/a | no reference model |

Every difference traces to the mapping, not to the converter. Each was checked with a query on the
built tables:

- **stg_orders.** The XML swaps dollars and cents (`EXP_ORDERS.SUBTOTAL -> STG_ORDERS.SUBTOTAL_CENTS`
  and so on). On all 61,948 orders, `gen.subtotal_cents * 100 = ref.subtotal_cents` and
  `gen.subtotal = ref.subtotal * 100`, and the same holds for tax and total. The 483 rows that match
  are the 483 orders with all-zero amounts. Day truncation agrees: `gen.ordered_at_day = ref.ordered_at`
  on every row.
- **stg_products, products and order_items.** `PRODUCT_PRICE` and `PRODUCT_PRICE_CENTS` are swapped
  in `EXP_PRODUCTS -> STG_PRODUCTS`. On every row, `gen.product_price = ref.product_price * 100`.
  `is_food_item` and `is_drink_item` agree (1/0 against true/false).
- **customers.** The marts aggregate the swapped `STG_ORDERS.ORDER_TOTAL`, so on all 935 customers
  `gen.lifetime_spend = ref.lifetime_spend * 100`. Order counts, first order date, name and
  customer type agree on every row.
- **stg_supplies and supplies.** `is_perishable` is 0 on all 65 rows, while the reference has 30
  true. The mapping tests `PERISHABLE = 'Y' OR PERISHABLE = '1'`, but the data holds booleans.
  This is a data and logic mismatch in the mapping.
- **stg_locations.** Every store gets `region = 'AMER'`. The seed tax rates are 0.04 to 0.08, all
  below the mapping's 0.09 threshold. All 6 stores survive the Router plus Union exactly once.
- **ENRICHED models** have no reference. Two findings:
  - `store_daily_kpis.unique_customers` sums to 0. This is the variable-order issue above.
  - The order count is 61,465, not 61,948, because 483 orders have no items, and the Master Outer
    joins keep the item-aggregate (detail) side.

**Not proven.** dbt success plus these row checks show that the SQL runs and matches the mapping
on this data. They do not prove equivalence with a PowerCenter run, because there is no PowerCenter
output to compare against.

## Coding agent mistakes (genuine, with how each was caught)

1. **Recon misread the unconnected columns (Hayden, recon §5.2).** The recon claimed `CUSTOMER_360`
   had 9 unconnected target columns. It had truncated a dump line at 400 characters and read the
   truncated line as complete.
   - Caught by Hashi, who re-probed the connectors with lxml during planning: 19 of 19 columns are
     connected.
   - The unconnected-column policy stays, but it is tested on a synthetic fixture instead.
2. **Recon called a pure expression stateful (Hayden, recon §5.3).** The recon said
   `EXP_MARK_FIRST_CUSTOMER` was stateful.
   - Caught by Hashi applying the documented top-to-bottom variable evaluation order: every
     variable reads only earlier variables, so the transformation is pure. Only
     `EXP_CUSTOMER_ORDER_NUM.v_ORDER_SEQUENCE` is stateful.
   - The correction changed the expected model split to 14 complete and 2 incomplete. The build
     confirmed that split.
3. **Hollow joiner test (Ahsoka, build).** The test "Master Outer keeps detail rows whatever the
   condition order" still passed after `master, detail = sorted(...)` was mutated to
   `master, detail = pair`. Both upstream key columns were named `K`, so a swapped side produced the
   same SQL.
   - Caught by mutation testing.
   - Fixed by giving the master key its own column name (`MK`). The mutation is now caught.
4. **Comparison artifact (Ahsoka, validation).** The first `compare.py` run reported `is_food_item`
   and `is_drink_item` as different. It had normalized booleans to `'1'` but decimals to `'1.0'`.
   - Caught by reading the rows behind the count.
   - Fixed in the normalization, without touching the converter.
5. **An `ERROR()` input default was kept as a warning (Hayden, recon §8 S16).** On 2026-11-01
   Hayden read a search excerpt ("the output default value of input/output ports is always
   ERROR('Transformation Error')") and concluded that `EXP_LOCATIONS.TAX_RATE` and
   `EXP_ORDER_ITEMS.ORDER_ID` carried that fixed default, so keeping the rows was correct. That was
   documented in this README.
   - Caught when the browser-read docs said that I/O-port defaults handle null input, and a survey
     of the export showed those two ports were the only non-blank ones out of 188.
   - Fixed in defect loop 1 (`docs/02-build.md`): such rows are now dropped, with a test that fails
     on the old code.

## Agent workflow

The work followed the Hayden orchestration (`.claude/commands/hayden.md`), with agents run strictly
in sequence:

1. **Hayden (orchestrator)** did the reconnaissance (`docs/00-recon.md`) and wrote the briefs
   (`prompts/subagent-briefs.md`). Hayden does not write code.
2. **Hashi (planner and critic)** verified the recon against the XML, chose the architecture and
   attacked its own plan (`docs/01-plan.md`, verdict READY WITH RISKS).
3. **Ahsoka (builder and reviewer)** checked the plan against the XML, wrote the end-to-end proof
   test and saw it fail before any code existed. She then built the steps in order, reviewed her own
   diff and mutation-tested it (`docs/02-build.md`).

Hashi and Ahsoka were real, separately spawned subagents. Each started with only its brief and
re-read the XML and code itself. They never ran in parallel. One more helper ran before planning:
a web-fetch agent tried to read the Informatica docs and could not reach them (see below).

Prompts, in order:

1. `prompts/initial-prompt.md`: the user's master prompt, verbatim.
2. The Hayden skill text, pasted by the user ("this is the skill and the agent orchestration that I
   want to have in my folder"). It is stored verbatim as `.claude/commands/hayden.md`.
3. "attaching the PDF's". The attachments never reached the cloud sandbox.
4. "I want to start clean remove everything that you have created in the repo except from the agents
   orchestration". The repo was reset to `.claude/commands/hayden.md` only, and the work restarted.
5. The user named the two references as the docs.informatica.com Designer Guide and Transformation
   Guide pages, and asked to continue with the master prompt.
6. "continue with Ahsoka once Hashi finishes", plus a request to remove a `prompts/README.md` file,
   and confirmation that this top-level README stays.
7. `prompts/subagent-briefs.md`: the briefs Hayden gave Hashi and Ahsoka. Each was sent together
   with the Law and Field-sense sections of the skill, verbatim. Brief 3 is defect loop 1 (of a
   maximum of 2).
8. Follow-ups from the user:
   - "create a pull request";
   - the demo repo path and a description of PowerCenter;
   - "I have added docs.informatica.com to the env allowed domains";
   - a request for a status brief.
9. `prompts/external-consult-informatica-docs.md`: a prompt Hayden wrote, at the user's request, for
   an online Claude with a browser to read the Informatica pages that the sandbox could not reach.
   The user relayed its answers back. They confirmed IIF and variable order, left unconnected target
   columns unconfirmed, and exposed the `ERROR()` default defect (mistake 5).

Environment limits that shaped the work:

- `hayden.boyanbalevengineering.com` was blocked at first, so the skill was installed from the
  user's pasted text. Once the domain was allowed, the installed file was compared with the published
  page: 183 of 185 substantive lines appear verbatim. The other 2 contain `<mission>`-style
  placeholders that the HTML comparison could not match, so they are unconfirmed rather than
  different.
- `docs.informatica.com` was blocked at first. After the user allowed it, the site's CDN still
  answered 403 to command-line clients. The fetch tool could not resolve the host in this session,
  and bypassing the bot check was rightly refused.
- PowerCenter semantics were checked against search-engine excerpts of docs.informatica.com pages,
  with their URLs listed in `docs/00-recon.md` §8. Anything that could not be confirmed that way is
  marked **[U]** above.

Deviations from the plan, with reasons, are in `docs/02-build.md`.
