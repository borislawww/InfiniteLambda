# 00 — Recon (Hayden)

Date: 2026-10-29. Size: **Deep**. This is a greenfield multi-module converter whose output is data logic, so a wrong translation is silent wrong data (Law 12).

Legend: **[V]** verified this session by reading the artifact; **[U]** unverified, needs checking or is recalled from memory.

## 0. Environment and premise

- [V] Repo `/home/user/InfiniteLambda` is empty: no commits, no code, no gates. Branch `claude/admiring-einstein-qg9icr`. So there is no baseline test suite and no neighbor code to mirror. The style spec is the master prompt plus the dbt reference projects.
- [V] Python 3.13.17. `lxml 6.1.3` and `defusedxml 0.7.1` are already installed. PyPI is reachable (a `dbt-duckdb 1.11.1` wheel downloaded). GitHub clone works.
- [V] **Blocked by egress policy (403):** `hayden.boyanbalevengineering.com` and `docs.informatica.com`. The Hayden skill was installed from the text the user pasted.
- [V] The user identified the two reference "PDFs" as the docs.informatica.com Designer Guide and Transformation Guide.
  - That host is denied from the sandbox, both directly and through the web-fetch agent.
  - Semantics were checked against web-search excerpts of docs.informatica.com pages instead. **§8 is authoritative for semantics** and supersedes [U] marks in §5 where it says [D].
- [V] The XML was fetched to the scratchpad, not the repo: `FLOWLINE_DEMO_JAFFLESHOP.xml`, 728,727 bytes, 4,701 lines. It needs to be committed as a test fixture.

## 1. XML facts [V]

- Header: `<?xml version="1.0" encoding="Windows-1252"?>` and `<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">`. The DTD is not available, so the parser must not load it.
  - lxml with `resolve_entities=False, load_dtd=False, no_network=True` parses the file cleanly.
- Hierarchy: `POWERMART > REPOSITORY(REPO_Jaffleshop, DATABASETYPE=Oracle) > FOLDER(FLOWLINE_DEMO_JAFFLESHOP)`.
- Folder children, in document order: `SOURCE`, `TARGET`, `MAPPING`, `CONFIG`, `SESSION`, `WORKFLOW`. Sources and targets are interleaved with mappings.
- Sources: 12 definitions.
  - `RAW_{SUPPLIES,STORES,PRODUCTS,ORDERS,ITEMS,CUSTOMERS}` have `DBDNAME=XEPDB1_Oracle_32_RAW` and `OWNERNAME=RAW`.
  - `STG_{LOCATIONS,SUPPLIES,PRODUCTS,ORDER_ITEMS,ORDERS,CUSTOMERS}` have `DBDNAME=XEPDB1_Oracle_32_STG` and `OWNERNAME=STG`.
- Targets: 16 definitions, which equals 16 target instances.
- All transformations are **non-reusable and defined inside `MAPPING`**. There are no folder-level transformations, mapplets, shortcuts, Lookups or Sequence generators.
- Instance resolution:
  - `INSTANCE TYPE=SOURCE|TARGET|TRANSFORMATION` carries `TRANSFORMATION_NAME` (the definition) and `NAME` (the instance). They differ for duplicates: `RAW_PRODUCTS1 → RAW_PRODUCTS`, `STG_ORDERS1 → STG_ORDERS`, `ORDER_FINANCIALS1 → ORDER_FINANCIALS`.
  - A Source Qualifier instance links to its source instance via `ASSOCIATED_SOURCE_INSTANCE`, and also via connectors.
- Ports: `TRANSFORMFIELD` has `PORTTYPE`, `DATATYPE`, `PRECISION`, `SCALE` and `EXPRESSION` (the expression equals the port name for pass-through ports).
  - `PORTTYPE` values seen: `INPUT`, `OUTPUT`, `INPUT/OUTPUT`, `LOCAL VARIABLE`, `INPUT/MASTER`, `INPUT/OUTPUT/MASTER`. `MASTER` marks a Joiner's master-pipeline port.
  - Others: Router and Union ports carry `GROUP`; Router outputs carry `REF_FIELD`; Aggregator group-by ports have `EXPRESSIONTYPE=GROUPBY`; Sorter key ports have `ISSORTKEY=YES` and `SORTDIRECTION`.
- Attributes: `TABLEATTRIBUTE NAME/VALUE`. Router groups are `GROUP NAME TYPE=INPUT|OUTPUT|OUTPUT/DEFAULT ORDER EXPRESSION`.
- `CONNECTOR FROMINSTANCE/FROMFIELD/TOINSTANCE/TOFIELD` (plus instance types). There are 1,066 in total.
- Expressions contain `\r\n` line breaks inside string attributes, for example in `EXP_LOCATIONS.REGION`.
- Sessions: `Enable high precision = NO` in all 3, so PowerCenter processes Decimal ports as Double [U semantics]. `Treat source rows as = Insert`.
  - Target instances in staging have `Pre SQL = TRUNCATE TABLE STG.<t>`, which means full refresh and maps to a dbt `table` materialization.
- Mapping parameter `$$RUN_DATE` (date/time, default `12/31/2025 00:00:00`) exists only in `m_FL_JS_MARTS_ENRICHED`. The ENRICHED session also references a parameter file that we do not have.
- The workflow is `Start → s_STAGING_FULL → s_MARTS_CORE → s_MARTS_ENRICHED → cmd_FL_JS_CREATE_RPT_VIEWS`.
  - The last task is a Command task running sqlplus on an external `.sql` file that is not in the export. It is out of scope.
  - That command line contains a plaintext DB credential. It is not reproduced in any of our docs.

## 2. Mapping dependency overview [V]

| Mapping | Reads | Writes (targets) |
|---|---|---|
| m_FL_JS_STAGING_FULL | RAW_* (6 defs, 7 instances: `RAW_PRODUCTS` twice) | STG_CUSTOMERS, STG_LOCATIONS, STG_ORDERS, STG_ORDER_ITEMS, STG_PRODUCTS, STG_SUPPLIES |
| m_FL_JS_MARTS_CORE | STG_* (12 SQ instances) | CUSTOMERS, ORDERS, ORDER_ITEMS, PRODUCTS, SUPPLIES |
| m_FL_JS_MARTS_ENRICHED | STG_* only | CUSTOMER_360, ORDER_FINANCIALS, ORDER_ITEM_ENRICHED, PRODUCT_DAILY_PERFORMANCE, STORE_DAILY_KPIS |

- The cross-mapping link is **by name**: a `SOURCE STG_X` (owner STG) in a marts mapping matches the `TARGET STG_X` written by staging.
- Data dependencies: CORE depends on STAGING, and ENRICHED depends on STAGING. ENRICHED reads **no** CORE output; the workflow runs it after CORE but there is no data dependency.
- A dbt DAG therefore derives naturally: one model per target, with STG sources resolved as `ref()` to the generated model and RAW sources as `source()`.

## 3. Transformation inventory [V]

| Type | STAGING | CORE | ENRICHED | Notes |
|---|---|---|---|---|
| Source Qualifier | 7 | 12 | 9 | All have empty Sql Query, User Defined Join and Source Filter, and Select Distinct=NO. Each has exactly 1 associated source. |
| Expression | 8 | 4 | 13 | Includes LOCAL VARIABLE ports (pure, and **stateful**, see traps). |
| Sorter | 6 | 2 | 7 | All ascending. None feeds an Aggregator or Joiner with Sorted Input=YES. One feeds a stateful Expression. |
| Joiner | 1 | 7 | 8 | Types used: `Normal Join` and `Master Outer Join` only. Each condition is one equality, `A = B`. Chained joiners exist. |
| Aggregator | 0 | 4 | 5 | All Sorted Input=NO. Functions: SUM, COUNT(port), COUNT(*), MIN, MAX, SUM(IIF(...)). |
| Router | 1 | 0 | 1 | `RTR_REGION`: 3 groups plus DEFAULT1, which is unconnected. `RTR_ORDER_VALUE_BAND`: HIGH_VALUE (`ORDER_TOTAL >= 50`), STANDARD (`< 50`), DEFAULT1 unconnected. |
| Custom Transformation | 1 | 0 | 1 | `Module Identifier=pmuniontrans` means a **Union** transformation. It has 2–3 input groups and 1 output group, and input groups map to outputs by port order within the group [U]. |
| Filter | 0 | 0 | 1 | `FIL_LOCS: NOT ISNULL(LOCATION_ID)` |

Expression functions in use:
- Functions: `IIF` (nested), `ISNULL`, `NOT`, `AND`, `OR`, `UPPER`, `LTRIM`, `RTRIM`, `TRUNC(date)`, `TO_DATE`, `TO_CHAR(date, fmt)`, `DATE_DIFF(d1, d2, 'DD')`, and `$$RUN_DATE`.
- Operators and literals: arithmetic `+ - * /`, comparisons `= >= <= < >`, string literals in single quotes, and numeric literals.
- Not used: DECODE, NVL, SUBSTR, INSTR, CONCAT, `||`, ADD_TO_DATE.

## 4. Reference projects [V]

- `dbt-labs/jaffle_shop_duckdb` (mandated) is the **classic** Jaffle Shop.
  - Seeds: `raw_customers(id, first_name, last_name)`, `raw_orders(id, user_id, order_date, status)`, `raw_payments`. Models: stg_customers, stg_orders, stg_payments, customers, orders.
  - dbt ≥1.11 with dbt-duckdb, `profiles.yml` → `jaffle_shop.duckdb`; staging models are views, marts are tables.
  - **Its schema does not match the XML.** Row-level comparison against it is impossible.
- `dbt-labs/jaffle-shop` (the current one) **matches the XML domain**.
  - Seeds `seeds/jaffle-data/raw_{customers,orders,items,products,stores,supplies}.csv` hold about 154k rows.
  - Staging: stg_customers, stg_locations, stg_orders, stg_order_items, stg_products, stg_supplies.
  - Marts: customers, orders, order_items, products, supplies, locations.
  - It requires dbt ≥2.0 and `dbt_utils` (`generate_surrogate_key` in stg_supplies). `cents_to_dollars` is `(x/100)::numeric(16,2)`, and `dbt.date_trunc('day', ...)` is used.
  - Source: `source('ecom', 'raw_*')`, with schema `raw`.
- Seed data shapes that matter: `raw_supplies.perishable` holds `True`/`False`, `raw_products.type` holds lowercase `jaffle`/`beverage`, and orders run from 2024-09 onward with integer cents.

## 5. Traps (what the XML knows and the task text doesn't)

1. **Connectors are cross-wired on purpose, or by bug.** Target column semantics must follow the CONNECTOR, never port names. [V]
   - `SRT_ORDERS → STG_ORDERS`: `SUBTOTAL→SUBTOTAL_CENTS` and `SUBTOTAL_CENTS→SUBTOTAL`, and the same for TAX_PAID and ORDER_TOTAL.
   - `EXP_PRODUCTS → STG_PRODUCTS`: `PRODUCT_PRICE ↔ PRODUCT_PRICE_CENTS`.
   - `EXP_RFM_SEGMENT → CUSTOMER_360`: LIFETIME_SPEND/PRETAX/TAX_PAID rotated.
   - `EXP_PRODUCT_METRICS → PRODUCT_DAILY_PERFORMANCE`: `MARGIN_PCT ↔ PERISHABLE_SALES_FLAG`.
   - Downstream mappings then compute from the swapped columns. For example, CORE `EXP_ORDERS_CALC.SUBTOTAL_RECAL = SUBTOTAL_CENTS/100` reads the column that actually holds dollars.
2. **Unconnected target columns.**
   - `CUSTOMER_360` has 19 columns but only 10 are connected. AVG_ORDER_VALUE, HIGH_VALUE_ORDERS, CUSTOMER_TYPE, RECENCY_DAYS, R/F/M_SCORE, RFM_SCORE and RFM_SEGMENT receive nothing, even though upstream ports compute them. [V]
   - Other targets have not been checked for this. [U]
3. **Stateful LOCAL VARIABLE ports** carry previous-row state, so they depend on Sorter order. [V] The patterns are:
   - `EXP_CUSTOMER_ORDER_NUM` (CORE and ENRICHED);
   - `EXP_MARK_FIRST_CUSTOMER` (ENRICHED).
   - Port display order is `v_PREV_CUSTOMER_ID` before `v_ORDER_SEQUENCE`. If PowerCenter evaluates variable ports top to bottom [U], the comparison always sees the *current* row, so the sequence would not reset per customer.
   - A converter must not "fix" this into `row_number()`, nor silently translate it.
4. **Pure LOCAL VARIABLE ports**, such as `v_TAX_RATE_CL = IIF(ISNULL(TAX_RATE),0,TAX_RATE)`, can be inlined into the outputs that reference them. Variables reference other variables, for example `v_MARGIN_CALC` uses `v_SUPPLY_COST_CL`.
5. **Union is a Custom Transformation.** Only the `pmuniontrans` module identifier distinguishes it from arbitrary C code. Any other Custom Transformation is unsupported.
6. **Router semantics** [U]:
   - A row goes to *every* group whose condition is TRUE.
   - The default group receives rows matching no group.
   - A NULL condition is not TRUE.
   - Unconnected groups drop their rows. For example, `ORDER_TOTAL IS NULL` drops out of RTR_ORDER_VALUE_BAND.
7. **Joiner master/detail** [U]:
   - `Master Outer Join` keeps all **detail** rows plus matching master rows, which is `detail LEFT JOIN master`.
   - `Detail Outer` is the mirror image.
   - The master side is the pipeline whose ports are `*/MASTER`. The join condition can name either side first (`ORDER_ID1 = ORDER_ID` vs `ORDER_ID = ORDER_ID1`).
   - Informatica Joiner conditions do not match NULL keys [U].
8. **Date logic.**
   - `ORDERED_AT_DAY = TO_DATE(TO_CHAR(ORDERED_AT,'DD-MM-YY'),'DD-MM-YY')` truncates to the day via a 2-digit year. YY century handling is [U]; it is irrelevant for 2016–2025 data, but must be documented.
   - `TRUNC(date)` truncates to the day.
   - `DATE_DIFF(d1, d2, 'DD')` gives d1 − d2 in days [U sign and fractional behavior].
9. **Types.** Integer division does not exist in the source mapping (ports are double or decimal), but DuckDB `int / int` gives a double [U check]. High precision is OFF, and target columns are NUMBER(p,s). Whether we cast at the target needs a decision.
10. **Data vs. logic mismatch.** `EXP_SUPPLIES.IS_PERISHABLE = IIF(PERISHABLE = 'Y' OR PERISHABLE = '1', 1, 0)`, but the seeds hold `True`/`False`, so a faithful conversion yields 0 everywhere. This is a validation finding, not a converter bug.
11. **Sorters** feeding a stateful Expression matter. Sorters feeding targets or unsorted Aggregators/Joiners do not change set semantics. dbt tables have no row order.

## 6. Proposed scope (for Hashi to attack)

- **In scope:**
  - Parser covering everything listed in §1.
  - IR plus graph validation: missing references, bad endpoints, cycles, unsupported types, unconnected required input.
  - Source Qualifier: plain, plus filter, distinct and SQL override detection.
  - Expression, including pure variables inlined.
  - Filter, Router, Sorter (dropped, with a note), Joiner (4 types), Aggregator, and Union.
  - Expression translator for the functions in §3.
  - DuckDB SQL; one dbt model per target; `source()` for RAW and `ref()` for cross-mapping STG.
  - `sources.yml` and `schema.yml`.
  - CLI, pytest suites, and dbt validation in DuckDB against jaffle-shop seeds.
- **Explicitly unsupported (fail loud):**
  - Stateful variables.
  - Non-Union Custom Transformations.
  - SQL override and user-defined join, if present.
  - Functions outside the translator.
  - Mapplets, Lookup, Sequence, Update Strategy, Normalizer.
  - Parameter files.
  - Command tasks and workflow scheduling.

## 7. Locked decisions (Hashi may challenge with evidence)

- **D1 — Python, lxml, pytest.** The user prefers Python, lxml is installed, and its parser options give safe DTD handling. No new XML dependency.
- **D2 — DuckDB is the SQL dialect.** Both reference projects run on DuckDB, and it is the only engine executable here.
- **D3 — One dbt model per target instance**, named after the target definition in lower case.
  - RAW sources become `source('<owner lower>', '<table lower>')`.
  - A source whose name matches a target generated in the same export becomes `ref()`.
  - This is the only architecture in which cross-mapping dependencies survive.
- **D4 — Connectors are the truth.** Port names are not. Suspicious wiring (a target column fed by a differently named port whose name matches another target column) is reported as a warning. It is never "corrected".
- **D5 — No silent wrong data.**
  - Anything untranslatable makes that model INCOMPLETE: a diagnostic naming mapping, transformation, port and expression.
  - The CLI exits non-zero.
  - An incomplete model must never be written as runnable SQL that looks complete.
- **D6 — Validation target.**
  - Row comparison runs against `dbt-labs/jaffle-shop` seeds and its models. It has the same domain and schema.
  - `jaffle_shop_duckdb` was inspected and is documented as schema-incompatible.
  - The validation harness may live under `validation/` and is not converter output.
- **D7 — Mission artifacts** go in `docs/` (00/01/02) and prompts in `prompts/`. This overrides the skill's `.hayden/` path, per the user's instruction.

## 8. PowerCenter semantics: primary-source check

Method: web-search excerpts restricted to `docs.informatica.com` (PowerCenter 10.4–10.5.x pages). The pages themselves cannot be fetched from the sandbox. **[D]** marks a claim confirmed by a doc excerpt; **[U]** marks a claim still unverified.

| # | Claim | Status | Source |
|---|---|---|---|
| S1 | **Joiner, Master Outer:** keeps all detail rows plus the matching master rows, and discards unmatched master rows. In SQL, `detail LEFT JOIN master`. **Detail Outer** is the mirror image. | [D] | `.../10-5/transformation-guide/joiner-transformation/defining-the-join-type/master-outer-join.html`, `.../detail-outer-join.html` |
| S2 | **Joiner:** NULL keys never match, even NULL against NULL. | [D] (added 2026-11-01) | `.../10-4-0/transformation-guide/joiner-transformation/defining-a-join-condition.html` |
| S3 | **Router:** a row goes to *every* group whose condition is TRUE. The default group gets a row only when all conditions are FALSE. An unconnected group discards its rows. | [D] | `.../transformation-guide/router-transformation/working-with-groups/output-groups/the-default-group.html`, `.../user-defined-groups.html` |
| S4 | **Union:** behaves like UNION ALL with no dedupe. It has many input groups and one output group with matching ports. It is "developed using the Custom transformation". | [D] | `.../10-5/transformation-guide/union-transformation/union-transformation-overview.html` |
| S5 | **Union port matching:** the sample C code copies input port i to output port i, which suggests **positional** matching. | [D, weak] | `.../custom-transformation/.../step-3--fill-out-the-code-with-the-transformation-logic.html` |
| S6 | **Port evaluation order:** input ports first, then variable ports in dependency/display order, then output ports. Variables keep their values across rows, and initial values make counters possible. | [D] | `.../working-with-transformations/local-variables/guidelines-for-configuring-variable-ports/port-order.html` |
| S6a | **Consequence for `EXP_CUSTOMER_ORDER_NUM`:** `v_PREV_CUSTOMER_ID` is evaluated before `v_ORDER_SEQUENCE` reads it, so the comparison always sees the current row. | [D, by S6] | This is stateful logic, so the converter must refuse it. |
| S7 | **DATE_DIFF(d1, d2, fmt):** d1 − d2, positive when d1 is later. Returns a double, so it can be fractional. NULL if either input is NULL. | [D] | `.../10-5/transformation-language-reference/functions/date_diff.html` |
| S8 | **TO_DATE with 'YY'** takes the **current year's century** at run time (RR uses a sliding window). Outside TO_DATE, YY and RR are the same. A string that doesn't match its format skips the row. | [D] | `.../dates/dates-overview/dates-in-the-year-2000/difference-between-the-yy-and-rr-format-strings.html`, `.../to_date.html` |
| S9 | **TRUNC(date)** with no format sets the time to 00:00:00 and returns NULL on NULL input. | [D] | `.../functions/trunc--dates-.html` |
| S10 | **NULL in Booleans:** ANSI behavior, so NULL AND FALSE = FALSE and NULL AND TRUE = NULL. A comparison with NULL returns NULL. A Filter condition that evaluates to NULL is treated as FALSE. | [D] | `.../constants/null/null-values-in-filter-conditions.html`, `.../filter-transformation/filter-condition.html` |
| S11 | **IIF with a NULL condition** returns value2, as in SQL `CASE WHEN`. This holds under the Integration Service default "Treat Null In Comparison Operators As = NULL". Under HIGH or LOW, the comparison itself is no longer NULL. | [D, by example] (2026-11-01, external Claude via browser) | `.../10-5/transformation-language-reference/functions/iif.html` (example table: SALES = NULL takes value2), `.../constants/null/null-values-in-comparison-expressions.html` |
| S12 | **Aggregates:** NULLs are ignored by default. SUM of an all-NULL group is NULL. COUNT(port) ignores NULLs and gives 0 when all are NULL; COUNT(*) counts every row. A server-level "treat nulls as zero" option exists, and we assume the default. | [D] | `.../aggregator-transformation/aggregate-expressions/null-values-in-aggregate-functions.html`, `.../functions/sum.html`, `.../functions/count.html` |
| S13 | **High precision off:** Decimal ports are processed as Double, so values over 15 digits lose precision. | [D] | `.../10-5/advanced-workflow-guide/high-precision-data/decimal.html` |
| S14 | **Unconnected target columns.** The docs say only "The Integration Service ignores unconnected ports". The value written (NULL or the DB column default) is not stated. | [U] (searched 2026-11-01) | `.../10-5/designer-guide/mappings/connecting-mapping-objects.html` |
| S15 | **Expression functions** (UPPER, LTRIM, RTRIM, ISNULL, TO_CHAR format tokens DD, MM, YY): standard behavior. | [U, low risk] | Not checked one by one. |
| S16 | **Input/output ports:** the output default is always `ERROR('Transformation Error')`, and a NULL input stays NULL unless the user sets an input default. A user-defined input `ERROR()` default skips NULL rows. | [D] (added 2026-11-01) | `.../10-5/transformation-guide/working-with-transformations/default-values-for-ports.html`, `.../10-5-8/.../skip-null-records.html` |
| S17 | **Oracle Number** (no precision) maps to Double (precision 15), and **Number(P,S)** maps to Decimal (up to 28). | [D] (added 2026-11-01) | `.../10-5/designer-guide/appendix-a--datatype-reference/oracle-and-transformation-datatypes.html` |

Update 2026-11-01: docs.informatica.com was added to the environment's allowed domains. The proxy now passes the connection, but the site's CDN answers 403 to command-line clients, and the fetch tool still cannot resolve the host in this session. S2, S16 and S17 were confirmed through search excerpts as before. S11 and S14 remain [U].

Update 2026-11-01, second pass: an online Claude with a browser read the 10.5 pages directly. Its full answer was relayed by the user; the prompt is `prompts/external-consult-informatica-docs.md`.
- **S16 corrected.** "For input/output ports, the Integration Service uses default values to handle null input values" (`.../default-values-for-ports/general-rules-for-default-values.html`). The system default shows as **blank**.
  - In this export, 186 of 188 Expression I/O ports have a blank DEFAULTVALUE. Only `EXP_LOCATIONS.TAX_RATE` and `EXP_ORDER_ITEMS.ORDER_ID` have `ERROR('Transformation Error')`, which makes them **user-defined** null-input defaults.
  - PowerCenter **skips** rows whose input is NULL there: "When you use the ERROR function as a default value, the Integration Service skips the row with the null value" (`.../user-defined-default-input-values/skip-null-records.html`).
  - The converter kept those rows, which is a defect. It has no effect on the sample data, which holds no NULLs in those columns. It was returned to Ahsoka (defect loop 1).
- **S6 and S6a confirmed directly.** "The Integration Service evaluates the input ports first ... the variable ports next, and the output ports last", and display order equals evaluation order (`.../port-order.html`).
  - Variable initial values are 0, '' and 01/01/0001 (`.../variable-initialization.html`).
- **S4 and S5.** Union "does not remove duplicate rows" is confirmed. Port matching is not stated explicitly. Groups are created from one shared port list, and the converter uses the export's explicit FIELDDEPENDENCY map, so it does not rely on either reading.
