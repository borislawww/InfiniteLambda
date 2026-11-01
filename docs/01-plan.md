# 01 — Plan (Hashi)

Date: 2026-10-30. Inputs: `docs/00-recon.md`, `prompts/initial-prompt.md` (master prompt), the XML at `SP/xml/FLOWLINE_DEMO_JAFFLESHOP.xml`, `SP/jaffle/jaffle-shop` (commit `5beb145b`), `SP/jaffle/jaffle_shop_duckdb` (commit `20cc9043`). SP = the session scratchpad. Every probe below was run this session with lxml or DuckDB against the real files. The probe scripts are in `SP/hashi/`.

Legend: **[V]** verified this session; **[U]** unverified, so the behavior is an explicit, tested assumption that the README must list.

## 0. Verification of recon

| Item | Result | Evidence |
|---|---|---|
| U1 Union port mapping | **[V] Resolved by explicit metadata.** Each Union `TRANSFORMATION` carries `FIELDDEPENDENCY INPUTFIELD=… OUTPUTFIELD=…`: 15 in UN_REGIONS and 38 in UN_ORDER_BANDS. In both, the result agrees with position and with suffix-stripped name. **Use FIELDDEPENDENCY, not position or name.** A Union output port without exactly one dependency per input group makes the model INCOMPLETE. | `probe1.py`, `grep FIELDDEPENDENCY` |
| U2 Unconnected target columns | **[V] None, in all 16 targets.** CUSTOMER_360 has 19 of 19 columns connected, all from EXP_RFM_SEGMENT. **Recon §5.2 is wrong**: it claimed 9 unconnected columns, probably from a truncated dump line. No target port has fan-in. | `probe2.py` |
| U3 Joiner master side | **[V]** The master side is the side whose ports have `*/MASTER` in PORTTYPE. Conditions put the master port on the left in 10 joiners (`ORDER_ID = ORDER_ID1`) and on the right in 5 (`SKU1 = SKU`, `ORDER_ID1 = ORDER_ID`, `PRODUCT_ID1 = PRODUCT_ID`, `CUSTOMER_ID1 = CUSTOMER_ID`). Each side of every joiner is fed by exactly one upstream instance once Sorters are seen through. | `probe1.py`, ad-hoc probe |
| U4 DuckDB semantics | **[V]** In DuckDB 2.0.0, `/` always returns DOUBLE (`7/2=3.5`, and decimal/decimal also gives DOUBLE). `CASE WHEN NULL` takes the ELSE branch. `NULL AND FALSE = FALSE`. `sum(all null)=NULL`, `count(x)=0`, `count(*)` counts rows. A cast from double to DECIMAL that overflows **raises** an error, which is loud. `date_diff('second',a,b)/86400.0` gives 1.5 for 36 h. `strptime('%y')` pivots at 69 (68→2068, 69→1969). `cast(true as varchar)='true'`. No port, instance or column name in the XML is a DuckDB keyword, and all names match `[A-Za-z_][A-Za-z0-9_]*`. Tests run generated SQL through the `duckdb` Python package. | `SP/venv` with duckdb 2.0.0 |
| U5 dbt | **[V]** dbt-core 1.12.6 with dbt-duckdb 1.11.1 installs from PyPI. jaffle-shop builds on dbt 1.x after two changes: drop `require-dbt-version` and the `dbt-cloud` block, and replace `packages.yml` with `git: https://github.com/dbt-labs/dbt-utils.git, revision: "1.3.0"`. Results: `dbt seed` PASS=6 and `dbt build` PASS=40, ERROR=0. | `SP/hashi/ref/jaffle-shop` |
| U6 Remaining [U] semantics | Still [U]. Each one is given an explicit, tested converter behavior (§7). | — |

New XML facts that the plan depends on **[V]**:
- **Variable statefulness is decidable structurally.** By recon S6 [D], variables evaluate top to bottom, so a variable is stateful **only if it references itself or a variable at the same or a later position**.
  - **Stateful:** only `EXP_CUSTOMER_ORDER_NUM.v_ORDER_SEQUENCE`, in CORE and ENRICHED.
  - **Not stateful:** `EXP_MARK_FIRST_CUSTOMER`. All of its references point to *earlier* variables, so it is pure. A literal translation gives `IS_FIRST_CUSTOMER = 0` for non-null keys. This is a validation finding about the mapping, not a converter decision. Recon §5.3 is corrected.
- **Column-level lineage** shows the stateful port reaches exactly two target columns: `ORDERS.CUSTOMER_ORDER_NUMBER` (CORE) and `ORDER_FINANCIALS.CUSTOMER_ORDER_NUMBER` (ENRICHED). Expected result: **14 complete and 2 incomplete models.** (`lineage.py`)
- No non-joiner, non-union transformation takes inputs from more than one upstream instance. There are no unconnected input ports, no Sorter `Distinct=YES` and no Aggregator I/O port outside the group-by. Every transformation instance name equals its definition name.
- **Port DEFAULTVALUE:** OUTPUT ports carry `ERROR(...)`, `NULL` or `'0'`. Input-capable ports carry only `NULL` (EXP_ORDERS.ORDERED_AT), except two I/O ports with `ERROR('Transformation Error')`: EXP_LOCATIONS.TAX_RATE and EXP_ORDER_ITEMS.ORDER_ID. Whether that ERROR applies to NULL input is [U].
- **Types:**
  - Source types: varchar2, number, number(p,s), timestamp, date.
  - Target types: varchar2, number(p,s), number, date. Every target column is `NOT A KEY` and nullable, so **no schema tests can be derived from the metadata**.
- Functions in use: IIF, ISNULL, SUM, MAX, MIN, COUNT (including `COUNT(*)`), TRUNC (on dates only), UPPER, LTRIM, RTRIM, DATE_DIFF, TO_DATE, TO_CHAR. Operators in use: `= >= <= < > + - * /`, AND, OR, NOT. The only mapping parameter is `$$RUN_DATE`, a date/time with default `12/31/2025 00:00:00`.
- Seeds: column names in jaffle-shop `raw_*.csv` equal the XML RAW_* field names exactly, so validation can run on real data.

## 1. Requirements

**Functional:** convert every mapping (or the one selected) in a PowerCenter folder export into a dbt project. Each target instance becomes one model, built only from connector-resolved lineage. Supported transformations are translated; anything else makes the model INCOMPLETE, with a precise diagnostic. The CLI is in master prompt §9. Tests are in §7.

**Non-functional**, limited to what this change touches:
- **Volume:**
  - Input is about 0.7 MB of XML (1,066 connectors, 3 mappings, 16 targets). The target is under 2 s end to end (estimate).
  - Validation data is 154k seed rows. The reference dbt build took 2 s (measured).
- **Determinism:** two runs on the same input give byte-identical files. There are no timestamps, and all iteration is ordered: topological order with ties broken by name.
- **Correctness bar:** zero silently wrong columns. Every untranslatable column ends in INCOMPLETE. Every assumption is either printed as a WARNING diagnostic or listed in the README.
- **Exit codes:** 0 = all complete. 2 = output written, but at least one model is INCOMPLETE. 1 = fatal (bad XML, invalid graph, unknown mapping, refusal to overwrite). When both apply, 1 wins.
- **Consistency, retries, concurrency, paging:** not applicable. This is a single-process batch CLI with no network, and nobody is paged; CI reads the exit code.

## 2. Approaches

**A. One CTE per transformation instance, demand-driven (chosen).**
- How it works:
  1. For each target, walk backward from its connected columns. Collect the ports each instance must produce: the needed columns plus *row-shaping* ports, meaning filter/router conditions, join keys, group-by ports and union groups.
  2. Emit one CTE per needed instance in topological order, selecting only the needed ports.
  3. Column names inside a CTE are that transformation's own port names. PowerCenter makes these unique per transformation, which is what solves the "joiner merges overlapping names" problem: the Joiner's ports are already distinct (`ORDER_ID` and `ORDER_ID1`).
  4. A shared upstream instance is one CTE that several consumers read, which is exactly PowerCenter's branching semantics, so nothing is double-counted.
  5. Sorters are transparent.
  6. Untranslatable ports fail only when demanded, so the dead `ITEMS_COUNT` is harmless and only 2 models become incomplete instead of 5.
- Steelman: it mirrors the graph one to one and is easy to explain and to diff against Designer.
- Main risk: CTE count and readability. Mitigated by pruning and transparent Sorters.

**B. Symbolic flattening.** Inline passive transformations into their consumers, and emit subqueries only at active boundaries (join, aggregate, union, filter, router).
- Steelman: the shortest SQL, closest to hand-written dbt.
- Rejected: it needs expression substitution across scopes and duplicated sub-expressions, which is the bug-prone part, for readability alone.

**C. One dbt model, ephemeral or intermediate, per transformation.** Rejected: it adds about 120 models of noise and its own naming contract, all for reuse nobody needs.

## 3. Scope

Files allowed:
- `pyproject.toml`, `.gitignore`, `README.md`
- `src/pwc2dbt/{__init__,__main__,ir,parser,expressions,compiler,sql,dbt_project,cli}.py`
- `tests/conftest.py`, `tests/test_{parser,graph,expressions,transformations,integration}.py`
- `tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml`
- `output/` (committed generated project)
- `validation/{run_validation.sh,compare.py,README.md}`
- `docs/02-build.md`

The module list is shorter than master prompt §10, but the responsibilities stay separate:
- parse: `parser`
- IR and graph validation: `ir`
- expressions: `expressions`
- transformation compilation: `compiler`
- SQL rendering: `sql`
- dbt files: `dbt_project`
- CLI: `cli`

**Non-goals** (each fails loud when present):
- workflows and sessions, Command tasks, parameter files
- mapplets, Lookup, Sequence, Update Strategy, Normalizer
- non-Union Custom transformations, reusable or shortcut transformations
- incremental models
- SQ SQL override, user-defined join and source filter (native Oracle SQL, which cannot be translated safely)
- Sorter Distinct, Aggregators without a group-by
- non-date mapping parameters, mapping variables
- TO_CHAR on numbers, LTRIM/RTRIM with a trim set, IIF with 2 arguments, DECODE, NVL, SUBSTR, INSTR, CONCAT and the other functions not used in the XML

**Cut for time:**
- `docs/architecture.md`, `conversion-rules.md`, `limitations.md` (the README covers them)
- session parsing (the high-precision flag and the parameter file are assumptions in the README)
- a keyword-quoting list (a clash fails loudly at dbt build)
- a heuristic warning for the "previous-row idiom placed in the wrong order" (documented instead)

## 4. Steps, in order

Write tests first, per §5 and §7. "Mirrors" names the convention followed. The repo is greenfield, so the neighbors are the master prompt and the jaffle-shop dbt style.

1. **`pyproject.toml`, `.gitignore`.**
   - `src/` layout. Runtime dependency: `lxml`. Dev extras: `pytest`, `duckdb`.
   - Script `pwc2dbt = pwc2dbt.cli:main`.
   - Ignore `.venv`, `validation/work/`, `*.duckdb`, `target/`, `dbt_packages/`, `logs/`.
   - Copy the XML to `tests/fixtures/`.
2. **`ir.py#*` (frozen dataclasses)**
   - Types: `Folder{sources, targets, mappings}`, `SourceDef{name, owner, fields}`, `TargetDef{name, fields}`, `Field{name, datatype, precision, scale}`, `Mapping{name, transformations, instances, connectors, parameters}`, `Transformation{name, type, ports, attributes, groups, field_dependencies}`, `Port{name, porttype, datatype, precision, scale, expression, group, ref_field, expression_type, default_value}` (with `is_input`, `is_output`, `is_master`, `is_variable` derived from PORTTYPE), `Instance{name, type, definition, associated_sources}`, `Connector{from_instance, from_field, to_instance, to_field}`, `Parameter{name, datatype, default}`.
   - `Diagnostic{severity: ERROR|INCOMPLETE|WARNING, mapping, transformation, port, message}`.
   - Transformations are keyed **per mapping**, never globally (trap: JNR_ADD_PRODUCTS exists in two mappings). Lookups by port name are case-insensitive (normalize to upper).
3. **`ir.py#validate_mapping(mapping, folder) -> list[Diagnostic]`**. Each check below yields an ERROR naming the mapping and the instance or port:
   - an instance definition that does not resolve;
   - a connector to or from an unknown instance or port;
   - direction violations (from an INPUT-only port, into an OUTPUT-only port);
   - fan-in on any input port;
   - a cycle (DFS over instance edges);
   - duplicate port names within a transformation;
   - any name used in SQL that fails `^[A-Za-z_][A-Za-z0-9_]*$` (injection and path guard, Law 9);
   - two target instances of one definition.
   An unsupported transformation type yields a WARNING here; it becomes INCOMPLETE only for the targets that depend on it.
4. **`parser.py#parse_file(path) -> Folder`**
   - `etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)`. lxml honours the Windows-1252 declaration. Parse from bytes.
   - Exactly one FOLDER is supported; otherwise ERROR.
   - Read SOURCE, TARGET, MAPPING > TRANSFORMATION/TRANSFORMFIELD/TABLEATTRIBUTE/GROUP/FIELDDEPENDENCY, INSTANCE (+ASSOCIATED_SOURCE_INSTANCE), CONNECTOR and MAPPINGVARIABLE.
   - Keep expression text verbatim. `\r\n` is whitespace for the lexer.
   - No line-based parsing.
5. **`expressions.py`**
   - `tokenize` uses regex *lexing* only: strings `'…'`, numbers, `$$NAME`, identifiers, operators `<> != >= <= = < > + - * / || ( ) ,`, and `*` inside COUNT.
   - `parse` is recursive descent into an AST: `Lit`, `Ref`, `Param`, `Call`, `Unary`, `Binary`. Precedence: unary −/NOT; then `* /`; then `+ − ||`; then comparisons; then NOT; AND; OR.
   - `translate(ast, ctx) -> str` raises `Unsupported(reason)`. `ctx` supplies: `resolve(name) -> (sql, datatype)`, `param(name) -> (sql, datatype)` and `allow_aggregates: bool`.
   - Function whitelist:
     - `IIF(c,a,b)` → `case when c then a else b end`
     - `ISNULL(x)` → `(x is null)`
     - `UPPER`, 1-argument `LTRIM`/`RTRIM`
     - `TRUNC(x)` → `date_trunc('day', x)`, only when the inferred argument type is date/time
     - `TO_CHAR(date, fmt)` → `strftime`
     - `TO_DATE(str, fmt)` → `strptime`
     - `DATE_DIFF(a, b, 'DD'|'D'|'DDD')` → `(date_diff('second', b, a) / 86400.0)`
     - `SUM`, `MIN`, `MAX`, `AVG`, `COUNT(x)` and `COUNT(*)`, only when `allow_aggregates` is set, and never nested
   - Format tokens: `YYYY YY MM DD HH24 MI SS` plus the separators `- / : . , space`. Anything else raises Unsupported. `YY` adds a WARNING (S8 century rule).
   - Operators map one to one (`!=` → `<>`).
   - A string literal containing `{{`, `{%` or `{#` raises Unsupported, because dbt would execute it as Jinja.
   - Anything else raises Unsupported. There is never a fallback.
6. **`compiler.py#compile_target(mapping, folder, target_instance) -> ModelResult{name, mapping, target_instance, status, ctes, final, refs, sources, diagnostics}`**
   - **(a) Demand pass.** `needed[instance] -> ordered set of ports` starts from the target's connectors. Port dependencies by type:
     - SQ: the source column through its connector.
     - Expression: refs in the output expression, with variables expanded. An I/O port depends on its own input.
     - Filter: the condition refs are always needed.
     - Router: an output port depends on its REF_FIELD input. Its group condition is always needed; the DEFAULT group needs every condition.
     - Sorter: transparent; resolve through it.
     - Joiner: pass-through, plus both join keys, always.
     - Aggregator: all GROUPBY ports, always, plus output expression refs.
     - Union: FIELDDEPENDENCY input in every group.
   - **(b) Single upstream.** Every input port resolves to `(upstream_cte, column)` through the connector alone (D4). `upstream_of()` is the only function that reads connectors. A non-Joiner/non-Union instance whose needed inputs come from more than one CTE becomes INCOMPLETE.
   - **(c) Per-type CTE builders:**
     - **SQ.** `select cast(<src col> as <duckdb type of SQ port>) as <port> from {{ source() | ref() }}`. A non-empty Sql Query, User Defined Join or Source Filter, or more than one associated source, makes the model INCOMPLETE. With `Select Distinct=YES`, the CTE is `select distinct` over **all connected** SQ ports and is not pruned.
     - **Expression.** Inline pure variables in parentheses. A variable referencing itself or a later variable is stateful: INCOMPLETE when needed, ignored when dead. A reference to an output port is unresolved: INCOMPLETE.
     - **Filter.** `where <cond>`.
     - **Router.** One CTE per *needed* group, `<rtr>__<group>`. A user group gets `where <cond>`. DEFAULT gets `where not coalesce(c1,false) and not coalesce(c2,false) …`.
     - **Joiner.** Normal → `inner join`. Master Outer → `from <detail> as d left join <master> as m`. Detail Outer → `from <master> as m left join <detail> as d`. Full Outer → `full outer join`. The condition is parsed with `expressions.parse` and must be a conjunction of `portA = portB` with one master and one detail port. `Case Sensitive String Comparison=NO` makes the model INCOMPLETE.
     - **Aggregator.** `group by` the group-by ports, with aggregate outputs translated with `allow_aggregates`. A non-group-by port referenced outside an aggregate, a variable, or the absence of any group-by makes the model INCOMPLETE.
     - **Union.** One `select` per input group mapped through FIELDDEPENDENCY, joined with `union all`, columns in output-port order. A non-pmuniontrans Custom transformation makes the model INCOMPLETE.
   - **(d) Final select.** `select cast(<up>.<col> as <target type>) as <target_col>` in target-field order. An unconnected target column becomes `cast(null as <type>)` with a WARNING and a SQL comment (S14 [U]).
   - **(e) D4 warning.** Raise a WARNING when a target column is fed by a differently named port whose name equals another column of the same target.
   - **(f) Input-port defaults.** `''` and `NULL` are no-ops. `ERROR(...)` gives a WARNING ("PowerCenter may reject rows with NULL X; generated SQL keeps them"). Any other value makes the model INCOMPLETE.
     - **Amended 2026-11-01 (defect loop 1).** The documentation says an `ERROR()` input default *skips* rows with NULL input. The converter now emits `where <input> is not null` in Expression, Filter, Router, Aggregator and Sorter, plus a WARNING. Joiner and Union go INCOMPLETE. `ABORT()`, or an unconnected input with `ERROR()`, goes INCOMPLETE. See `docs/00-recon.md` §8 "second pass" and `docs/02-build.md` "Defect loop 1".
   - Status is set **once**, here: COMPLETE, or INCOMPLETE with the list of missing columns and reasons.
   - Mirrors: jaffle-shop CTE style.
7. **`compiler.py` type maps.** These are the only two type boundaries; intermediate ports are never cast.
   - SQ port to DuckDB: string/nstring/text → `varchar`; integer/small integer → `integer`; bigint → `bigint`; double/real/**decimal → `double`** (high precision OFF, S13); date/time → `timestamp`.
   - Target to DuckDB: varchar2/varchar/char → `varchar`; `number(p,s)` → `decimal(p,s)`; `number` → `double` (Oracle NUMBER with no precision keeps fractions [U]); date → `timestamp` (Oracle DATE has a time part).
   - An unknown type makes the model INCOMPLETE.
8. **`compiler.py#compile_folder(folder, mappings) -> list[ModelResult]`**
   - Run `validate_mapping` first. An ERROR skips that mapping's models and forces exit 1.
   - Source resolution: if the source definition name equals a target definition that has a target instance in the export, emit `ref(lower(name))`; otherwise `source(lower(owner), lower(name))`.
   - **Incompleteness propagation:** iterate to a fixpoint over `refs`. A model that refs an INCOMPLETE model, or a model from a mapping that failed, becomes INCOMPLETE ("upstream model X incomplete").
   - Single-mapping runs report refs to models not generated in this run as a WARNING.
9. **`sql.py#render_model(result) -> str`**
   - Header: `-- Generated by pwc2dbt from mapping <M>, target instance <T>. Do not edit.`
   - Then `with` and the CTEs, blank-line separated with 4-space indent as in jaffle-shop, then the final `select … from <last cte>`.
   - Identifiers are lowercase and unquoted, and every column reference is qualified with its CTE name, because DuckDB lateral aliases would otherwise make name resolution ambiguous.
   - For INCOMPLETE models: a header block listing each missing column and its reason, and the select list without those columns. No placeholder NULL, ever.
10. **`dbt_project.py#write_project(results, folder, out_dir, overwrite)`**
    - Write `dbt_project.yml`: name = lower(folder) sanitized, `profile` = the same name, `models: +materialized: table` (Pre SQL TRUNCATE means full refresh).
    - Write COMPLETE models to `models/<lower mapping>/<model>.sql`, with one `schema.yml` per mapping dir: model description and columns with `data_type`, no tests, because the metadata has no keys.
    - Write `models/sources.yml`, grouped by owner, `schema: <owner lower>`.
    - Write INCOMPLETE models **only** to `incomplete/<model>.sql`, outside `model-paths`.
    - Hand-write the YAML with values quoted through `json.dumps`, so no new dependency is added.
    - Refuse a non-empty `out_dir` unless `overwrite` is set. With `overwrite`, delete only `models/` and `incomplete/` first, so stale complete-looking models cannot survive.
11. **`cli.py#main(argv) -> int`**
    - Flags: `--input`, `--output`, and exactly one of `--mapping NAME` or `--all-mappings`; plus `--overwrite`.
    - Print one line per mapping, one per model (`COMPLETE`, or `INCOMPLETE: <col>: <reason>`), then every diagnostic as `SEVERITY mapping/transformation.port: message`, then a summary line.
    - Exit 0, 2 or 1 as in §1. An unknown mapping lists the available ones.
    - `__main__.py` calls `main`.
12. **Generate `output/`** with `python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output output --all-mappings` and commit it. The integration test keeps it in sync.
13. **`validation/run_validation.sh` + `compare.py`**
    - Clone jaffle-shop at `5beb145b` into `validation/work/`. Apply the U5 changes. `dbt deps`, `dbt seed --vars '{load_source_data: true}'`, `dbt build` into `validation/work/jaffle.duckdb` (schemas `raw` and `main`).
    - Run the converter. Write a `profiles.yml` for the generated project pointing at the **same** DB with `schema: gen`. Run `dbt debug/parse/compile/build`.
    - `compare.py` matches each generated COMPLETE model to the reference model of the same name. For the common column set it reports the row counts of both sides and `EXCEPT ALL` counts in both directions, with numerics rounded to 2 dp. Results go to `validation/README.md`, with each divergence explained (§7 known list).
    - ENRICHED models have no reference counterpart, so they get build success plus row counts only.
14. **`README.md`** (master prompt §12): overview and architecture, install, commands, tests, one real generated model copied from `output/`, assumptions (§7 list), unsupported features, where output can be wrong, agent mistakes (recon §5.2 and §5.3 corrections, below), agent workflow and prompts.
15. **`docs/02-build.md`** (Ahsoka): evidence, mutation-test notes, deviations.

## 5. Proof of effect

The test that fails now (there is no code) and must pass after, in `tests/test_integration.py`:

**`test_real_xml_end_to_end`**:
- Converts the committed XML with `--all-mappings` into `tmp_path`. The exit code is **2**.
- 3 mappings are discovered.
- The model set equals lower(target definition names): 16 models, 14 in `models/` and **exactly** `orders` and `order_financials` in `incomplete/`. Each incomplete model's diagnostic names `EXP_CUSTOMER_ORDER_NUM`, `v_ORDER_SEQUENCE` and `CUSTOMER_ORDER_NUMBER`.
- Models of CORE and ENRICHED contain `ref('stg_…')`. STAGING models contain `source('raw', 'raw_…')`.
- It then **executes** the 14 complete models in DuckDB, in ref order, on a 2–3-row hand-made `raw.*` dataset, and asserts semantic values:
  - `stg_orders.subtotal_cents == 7.0` and `stg_orders.subtotal == 700` (connector swap honoured);
  - `stg_locations.region` is `'AMER'` for tax_rate 0.06, and all store rows survive router plus union;
  - `stg_supplies` drops a supply whose sku is not in products (Normal join), and `is_perishable == 0` for `'False'`;
  - `customer_360` columns follow the rotated wiring (`lifetime_tax_paid` holds the pretax sum).

Supporting proofs, in `tests/test_transformations.py`:
- `test_master_outer_join_keeps_detail_rows_condition_either_side`: two fixtures differing only in condition order (`M = D` and `D = M`) produce identical rows, keeping unmatched **detail** rows.
- `test_router_row_in_two_groups_and_default`.
- `test_dead_stateful_port_is_not_incomplete` and `test_needed_stateful_port_is_incomplete`.

In `tests/test_integration.py`:
- `test_committed_output_matches_regeneration`: byte equality, which covers determinism and serves as the snapshot regression.

## 6. Invariants

Each invariant is enforced exactly once.

| Invariant | Enforced at |
|---|---|
| Target column semantics come from connectors only (D4) | `compiler.upstream_of`, the only reader of connectors |
| Only COMPLETE models land in `models/` (D5) | Status is set once in `compile_target`. `write_project` is the single writer that picks a folder from that status. Propagation runs in `compile_folder`. |
| Transformation names are mapping-scoped | IR shape: `Mapping.transformations` (no global map) |
| Master/detail comes from PORTTYPE, never condition order | Joiner builder |
| Stateful variables are never translated | Variable-order check in the Expression builder |
| Safe identifiers (SQL, file paths) and no Jinja in literals | `validate_mapping` (names); `translate` (literals) |
| Determinism | Ordered iteration (topological order, ties by name); test via the committed `output/` |

- **Level touched:** new code (level 1–2) and generated files (level 3: the output dbt project is a contract read by the validation harness and by reviewers).
- **Door:** two-way. Greenfield; nothing outside the repo consumes the output.
- **Upstream:**
  - PowerCenter export format (repository version as exported; DTD absent, never loaded)
  - lxml 6.1.3
  - DuckDB 2.0.0
- **Downstream:**
  - dbt-core 1.12.6 and dbt-duckdb 1.11.1 (validated)
  - jaffle-shop @5beb145b, needing the dbt-utils git 1.3.0 workaround
- **Sideways:** none.
- **Rollout or skew window:** not applicable.

## 7. Edge and failure cases, and [U] assumptions

**Inputs and the CLI:**
- **Empty or invalid input:**
  - Malformed XML → exit 1 with the lxml message.
  - No FOLDER, or more than one → exit 1.
  - A mapping with no targets → WARNING, no models.
  - An unknown `--mapping` → exit 1 with the list of mappings.
- **XXE:** a fixture with `<!ENTITY x SYSTEM "file:///etc/passwd">` must not expand it.
- **Partial failure:**
  - A graph ERROR in one mapping still writes the other mappings. The failed mapping's dependants become INCOMPLETE through propagation, and the exit code is 1.
  - A stale model from an earlier run is impossible, because `--overwrite` clears `models/` and `incomplete/`.
- **Retry, concurrency, auth, version skew, migration:** not applicable to a local CLI. Re-running is idempotent: same input, same bytes.

**Data and SQL behavior:**
- **NULLs:**
  - IIF with a NULL condition takes the else branch (S11 [U], matches SQL CASE).
  - Filter or router condition NULL counts as FALSE (S10 [D]); a NULL router condition sends the row to DEFAULT (S3 [D]).
  - Joiner NULL keys never match (S2 [U]).
  - SUM of all NULLs is NULL; COUNT(port) skips NULLs (S12 [D]).
- **Division by zero:** DuckDB returns NULL, while PowerCenter raises a row error. Every division in the XML is guarded by IIF, so this is documented, not handled.
- **Real data volumes:** `dbt build` on the 154k seed rows (step 13).
- **[U] assumptions** the README must list, each with the converter behavior above:
  - S2, S11, S14, S15;
  - TO_DATE `YY` century (S8): DuckDB pivots at 69, while PowerCenter uses the current century. A WARNING is emitted; the result is identical for 2000–2068;
  - I/O `ERROR(...)` defaults: WARNING, rows kept;
  - the `number` target type maps to double;
  - output-port default values apply only to transformation errors, so they are ignored;
  - no intermediate rounding or casting for integer ports or string precision;
  - `$$RUN_DATE` becomes `var('run_date', '2025-12-31 00:00:00')` because the session parameter file is absent;
  - ref resolution by name only (a source and target with the same name in different DBs would be mis-linked);
  - source types after seed loading (`perishable` is a BOOLEAN that casts to `'true'`).
- **Known validation divergences** that the XML causes (expected, not converter bugs):
  - stg_orders and stg_products have cents/dollars swapped;
  - stg_supplies is inner-joined to products, with `is_perishable = 0`;
  - stg_locations has an extra `region`;
  - CORE joiners use Master Outer with the aggregate as detail, so result rows are aggregate-driven;
  - CUSTOMER_360 spend columns are rotated;
  - `STORE_DAILY_KPIS.UNIQUE_CUSTOMERS` is about 0 (pure variable order);
  - PRODUCT_DAILY_PERFORMANCE has `margin_pct` and `perishable_sales_flag` swapped.

## 8. Definition of done

- `pytest -q` is green, with all test levels in §7 of the master prompt present:
  - **test_parser:** loading, encoding and XXE, counts (3 mappings, 12 sources, 16 targets, 1,066 connectors), port extraction, instance resolution (`RAW_PRODUCTS1→RAW_PRODUCTS`), mapping-scoped duplicate names.
  - **test_graph:** linear, two pipelines, chained joiners, multiple targets sharing one SQ (the CTE is emitted once), router branching, cycle, missing instance or port, fan-in, multiple source instances.
  - **test_expressions:** precedence, nested IIF, CRLF whitespace, an unknown function is Unsupported, an aggregate outside an Aggregator is Unsupported, a Jinja literal is rejected, DuckDB-executed NULL, IIF, DATE_DIFF-fractional and TO_CHAR/TO_DATE cases.
  - **test_transformations:** a DuckDB-executed fixture per SQ (plus distinct, plus filter → INCOMPLETE), Expression (pass-through, variable inlining, stateful), Filter, Router, Sorter (no ORDER BY), Joiner (all 4 types, both condition orders, NULL keys), Aggregator (COUNT(port) vs COUNT(*), all-NULL SUM, SUM(IIF)), Union (FIELDDEPENDENCY wins over position, using a shuffled fixture), target cast and unconnected target column.
  - **test_integration:** §5, CLI exit codes 0, 1 and 2, overwrite refusal, committed-output equality, and a no-hardcoding test (`src/pwc2dbt` contains no mapping, target, port or `JAFFLE` name from the fixture).
- `output/` is committed and equals a fresh run.
- `validation/run_validation.sh` has been executed this session, with dbt debug/parse/compile/build results and the compare table recorded in `validation/README.md` and `docs/02-build.md`. Nothing is claimed unless it was run.
- The README covers every item in master prompt §12.

**Quality gates (master prompt §13) mapped to steps:**
- XML read dynamically: 4.
- 3 mappings and graphs: 2–4.
- Source and target relationships: 6 and 8.
- Supported translations: 5–7.
- Unsupported reported: 3, 6 and 11.
- Dynamic SQL: 6 and 9.
- Valid dependencies: 8, plus dbt parse in 13.
- CLI: 11.
- Parser, graph, expression, transformation, integration and negative tests: §8.
- SQL validation: DuckDB-executed tests.
- dbt validation: 13.
- No hardcoding: no-hardcoding test.
- No silent fallbacks: Unsupported, plus §5's incomplete assertions.
- No needless abstraction: attack log.
- Readable SQL: 9 and the committed output.
- Actionable errors: Diagnostic format in 11.
- Deterministic: committed-output test.
- README, assumptions, limitations, wrong-output scenarios, agent mistakes, prompts, workflow: 14.

**Time:** steps 1–12 take about 110 min, sized for one implementer. Steps 13–15 take about 35 min of validation plus the README. If implementation overruns, cut in this order:
1. Partial SQL in `incomplete/` (write the header only).
2. Detail Outer and Full Outer tests (keep the code).
3. Select Distinct support (make it INCOMPLETE instead).

## Attack log

1. **"Trust the recon numbers."** Recon §5.2 said CUSTOMER_360 has 9 unconnected columns. Re-probing with lxml shows 19 of 19 connected (`probe2.py`).
   - Change: the unconnected-column policy stays, but it is tested on a fixture, not on the real XML.
   - Logged as a genuine agent mistake for the README.
2. **"EXP_MARK_FIRST_CUSTOMER is stateful" (recon §5.3).** By S6 [D], its variables read only *earlier* variables, so it is pure, and a literal translation yields about 0 unique customers.
   - Alternative: refuse it anyway "to be safe". That would be the converter inventing semantics.
   - Held: translate it, and list it as a top risk and a validation finding. A structural "wrong-order idiom" warning was considered and rejected (Bar: the README covers it, and a heuristic adds code for one case).
3. **Column-level vs model-level incompleteness.** Model-level would also fail CUSTOMER_360, STORE_DAILY_KPIS and AGG-fed models whose rows merely *pass through* EXP_CUSTOMER_ORDER_NUM. That would be wrong: Expression is passive and does not change rows.
   - Held: demand-driven lineage. It costs one backward pass, which is also needed for pruning.
4. **Pruning breaks row semantics?** Traced every active type:
   - Filter and router conditions, join keys, group-by and union groups are *always* demanded.
   - Select Distinct is computed over all connected ports, so it is excluded from pruning.
   - Sorter Distinct makes the model INCOMPLETE.
   - Fixed in step 6a.
5. **Shared upstream and self-join.** One CTE serves many consumers. A self-join uses aliases `m` and `d`. Held.
6. **Joiner condition order.** 5 of 15 joiners name the master port second. The side is taken from PORTTYPE, and a test covers both orders. Held.
7. **Union by name or position.** FIELDDEPENDENCY makes the question moot. A shuffled fixture proves the metadata wins. Changed from recon's "positional [U]".
8. **Stale output** (under-build). `--overwrite` alone would leave an old `models/orders.sql` looking complete after a later INCOMPLETE run. Fixed: clear owned subtrees.
9. **Downstream of an incomplete model** (under-build). Without propagation, a ref to a missing model breaks `dbt parse` for the *whole* project. Fixed: fixpoint propagation in step 8.
10. **Boundary casting level.** The seed `perishable` is BOOLEAN, so `= 'Y'` would fail in DuckDB. The SQ is the boundary that owns "type of external data", so it casts to the SQ port type. The target owns the persisted type. Intermediate ports are not cast, since high precision OFF means doubles, which matches DuckDB `/`.
    - The alternative of casting every port was rejected: it adds rounding that PowerCenter does not do, plus noise.
11. **Injection** (Law 9). Identifiers from the XML go into SQL and file paths, and literals go into Jinja. Guarded by the identifier regex in `validate_mapping` and the Jinja check in `translate`.
12. **Over-build review:**
    - `sql.py` has one caller, but master prompt §6 mandates a separate rendering responsibility (user instruction outranks Law 5). Kept.
    - Detail Outer, Full Outer and Select Distinct are named explicitly in the master prompt and cost a few lines each. Kept, and first in the cut list.
    - A keyword-quoting list: no XML name clashes ([V]), and a clash fails loudly at dbt build. Cut.
    - Session parsing for the high-precision flag or the parameter file: speculative. Cut, and recorded in the README.
    - PyYAML: a dependency to save about 20 lines. Cut.
    - 3 extra docs files: duplicate the README. Cut.
13. **Folder layout vs jaffle-shop `staging/` and `marts/`.** Deriving folders from mapping names follows master prompt §5 ("determined by the mapping graph") and avoids name heuristics. Held.
14. **Argue for leaving it alone / a smaller scope.**
    - A model-level "refuse anything odd" converter would be smaller, but it would mark 5 of 16 models incomplete and lose most of the validation.
    - Approach B would be prettier and riskier.
    - The plan stays at A.

## Verdict: READY WITH RISKS

Top risks:
1. [U] semantics, listed in §7, especially the variable-order reading that makes UNIQUE_CUSTOMERS about 0, the `number` → double mapping, and the I/O `ERROR` defaults.
2. Time: about 110 min is tight for 7 modules plus about 40 tests. The cut order is in §8.
3. jaffle-shop runs only on dbt 1.x through the dbt-utils git workaround, which ties validation to the pinned commits.

Open question: none blocking. The recon errors (§5.2, §5.3) correct facts, not locked decisions D1–D7.
