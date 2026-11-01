# 02: Build and review (Ahsoka)

Date: 2026-10-30. Spec: `docs/01-plan.md` (READY WITH RISKS). Size: Deep. Python 3.13 in `.venv/`, with
lxml 6.1.3, DuckDB 2.0.0, pytest 9.2.0, dbt-core 1.12.6 and dbt-duckdb 1.11.1. Nothing was committed.

## Result

Done. All 15 plan steps are complete, except where a Deviation below says otherwise. On the real export,
the converter writes 14 COMPLETE models and 2 INCOMPLETE ones (`orders` and `order_financials`, whose
`CUSTOMER_ORDER_NUMBER` comes from the stateful `EXP_CUSTOMER_ORDER_NUM.v_ORDER_SEQUENCE`) and exits
with 2. This is exactly what the plan predicted.

## Plan checked against the XML before the first edit

Each item was re-probed with lxml in this session:

- **Source and target fields.** The source fields of RAW_* match the seed columns.
- **Port types.** Source Qualifier ports are `string`, `double`, `decimal` and `date/time`. Target types
  are `varchar2`, `number`, `number(p,s)` and `date`.
- **Sorter Distinct.** Sorter `Distinct` is `NO` everywhere.
- **Input-port defaults.** The only input-capable defaults are `NULL` (EXP_ORDERS.ORDERED_AT) and
  `ERROR('Transformation Error')` (EXP_LOCATIONS.TAX_RATE and EXP_ORDER_ITEMS.ORDER_ID). This matches
  the plan.
- **Join condition order.** The order varies across Joiners, as the plan says.
- **Union metadata.** Each Union carries FIELDDEPENDENCY.
- **Parameters.** `$$RUN_DATE` (`ISPARAM=YES`) exists only in ENRICHED.

No plan fact was contradicted.

## Evidence (every command was run in this session)

| Command | Exit code | Result |
|---|---|---|
| `pytest -q tests/test_integration.py::test_real_xml_end_to_end`, before any `src/` existed | 4 | **RED**: collection error, `pwc2dbt.cli` does not exist |
| The same test after steps 1–11 | 0 | **GREEN** |
| `pytest -q` | 0 | 86 passed |
| `python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output output --all-mappings --overwrite` | 2 | 14 complete, 2 incomplete, 0 errors, 16 warnings |
| `validation/run_validation.sh`, run twice, the second time after the review fixes | 0 | See the dbt results below |
| `python -m pyflakes src tests validation/compare.py` | 0 | Clean, after removing one unused import |

**dbt results.**

- Reference jaffle-shop @5beb145b:
  - `dbt seed`: PASS=6.
  - `dbt build`: PASS=43, NO-OP=3, ERROR=0.
- Generated project:
  - `dbt debug`: "All checks passed!"
  - `dbt parse`: 14 models, 6 sources.
  - `dbt compile`: OK.
  - `dbt build`: PASS=14, ERROR=0, on 154k seed rows. No DECIMAL overflow at the target precisions.

The comparison table and an explanation of each divergence are in `README.md`, section "Validation against
Jaffle Shop". Every difference traces to the mapping, and each was verified with a query on the built tables:

- dollars and cents swapped in stg_orders and stg_products, and propagated into customers and order_items;
- `is_perishable` tests for `'Y'`/`'1'`, but the data holds booleans;
- every store's region is `AMER`, because all tax rates are below 0.09;
- `unique_customers` is 0, because of the variable order;
- 483 orders have no items, so the aggregate-driven Master Outer joins drop them.

Shared columns with no divergence: stg_customers, stg_locations, stg_order_items, stg_supplies, supplies (0 / 0).

**Mutation testing.** 5 lines, each mutated, the targeted test run, then reverted with `SP/mutate.py`.
Caught **5 of 5**. On the first run only 4 of 5 were caught (see review finding 1).

| Line mutated | Caught by |
|---|---|
| Master Outer becomes `master left join detail` | `test_join_types_and_null_keys_never_match` |
| Stateful check `>= i` becomes `> i` | `test_needed_stateful_port_is_incomplete` |
| Router default `not coalesce(c, false)` becomes `not c` | `test_router_row_in_two_groups_and_default` |
| Joiner side `sorted(pair, by master)` becomes `pair` | `test_master_outer_join_keeps_detail_rows_condition_either_side` |
| Propagation `status == COMPLETE and blocked` becomes `False` | `test_incompleteness_and_failed_mappings_propagate_to_dependants` |

Both review fixes, finding 2 and finding 3, were also mutated off. Their new tests went red (3 failed), then green again once the fixes were restored.

## Deviations from the plan

1. **No `validation/README.md`.** The brief replaces plan step 13's file, because the user wants
   only the top-level README. The results are in `README.md` ("Validation against Jaffle Shop") and in
   this file.
2. **`compile_folder` returns `(models, mapping diagnostics)`, not `list[ModelResult]`.** Validation
   ERRORs and "ref not generated in this run" warnings belong to a mapping, not to a model, and the CLI
   needs them to print them and to choose exit code 1.
3. **`ModelResult` has a `columns` field** (target column, DuckDB type). `schema.yml` needs a `data_type`
   for every column, and the plan's field list had no place for it.
4. **`expressions.Context` has a `warn` callback.** The TO_DATE `YY` warning must carry its
   transformation and port, and translation is the only place that knows about it.
5. **The Joiner aliases are `master` and `detail`, not `m` and `d`.** The master prompt (§5) asks for
   meaningful aliases. DuckDB accepts both words; this was checked.
6. **Mapping variables (`ISPARAM=NO`) are not parsed as parameters.** A reference to one then fails
   translation with "mapping variables are not supported". This is the loud failure the plan's
   non-goal requires, with no extra code.
7. **The single-upstream check uses every connected input, not only the needed ones.** It is stricter
   and simpler, and has no effect on the sample. A dead input from a second pipeline would make the
   node INCOMPLETE where the plan would not.
8. **Two review fixes add behavior that the plan does not list.** They are findings 2 and 3 below.

## Review (read cold, as the person who signs it)

**Findings fixed:**

1. **A test was hollow (blocking).** The mutation `master, detail = pair` survived, because both upstream
   key columns were named `K`, so a swapped side rendered identical SQL. The fix gives the master key its
   own column name (`MK`) in the joiner fixture. The mutation is now caught.
2. **Silent wrong data under Select Distinct (blocking).** If a connected Source Qualifier port had an
   unmappable type, it was left out of the `distinct` row dependencies. The distinct then ran over fewer
   columns and returned fewer rows, with no diagnostic.
   - Fix: every connected port is a row dependency, so a problem in any of them makes the model INCOMPLETE.
   - New test: `test_select_distinct_with_an_untranslatable_connected_port_is_incomplete`.
3. **A target loaded by two mappings overwrote one model with the other (blocking).** The plan's check
   covered only two instances inside one mapping.
   - Fix: `compile_folder` now raises an ERROR, keeps the first model and leads to exit code 1.
   - New test: `test_a_target_loaded_by_two_mappings_is_an_error_not_an_overwrite`.
4. **Unused code (suggestion).** Removed the unused `deps_extra` parameter, an unused import, and an
   interim warning-sink hack (replaced by the `warn` callback).
5. **Comparison artifact in `compare.py` (suggestion).** Booleans were normalized to `'1'` and decimals
   to `'1.0'`, which made `is_food_item` look different. Fixed in the harness only.

**Findings left, each with the argument for leaving it:**

- **The XXE test cannot tell our parser flags apart from lxml 6's defaults (suggestion).** With
  default options, lxml 6.1.3 already refuses external entities ("Entity 'x' not defined"), so removing
  `resolve_entities=False` would not turn the test red.
  - Left as is: the explicit flags protect against older lxml versions and against a future change of
    defaults, and the test still guards the observable behavior.
  - Smallest relief: a test that asserts the parser options. That would test configuration rather than
    behavior.
- **Sorter ports are not checked for input default values (suggestion).** Because Sorters are
  transparent, an `ERROR()` or constant default on a Sorter port would go unreported.
  - No Sorter port in the sample has a default.
  - The README lists Sorter Distinct; this is a related gap.
  - Fixing it means another walk in `upstream_of`. Leave it until an export has such a port.
- **`compiler.py` is about 790 lines (suggestion).** It is one module with one builder per
  transformation type.
  - Splitting it into a package of builders adds files and imports, with no caller that benefits.
  - The plan's module list is kept.
- **A theoretical CTE-name collision (rejected).** An instance named `A__B` and a router `A` with group
  `B` would produce the same CTE name. No real export does this, the cost is a loud dbt error, and the
  only argument for fixing it is symmetry.

**Other review checks:**

- **Correctness walk.** Each builder was walked with these inputs:
  - **Empty or no input:** an Expression with no connected input is INCOMPLETE ("no connected input"),
    never a one-row `select`. A target with no connected column is INCOMPLETE.
  - **Failing input:** every `Unsupported` becomes a port or node problem, and only demanded ones count.
  - **Partial failure:** a mapping with an ERROR is skipped, the other mappings are still written,
    its dependants become INCOMPLETE, and the exit code is 1.
  - **Concurrency:** none; this is a single-process CLI.
- **Style.** The repo is greenfield, so the neighbors are the master prompt and jaffle-shop:
  - CTE layout with blank lines inside the parentheses, as in jaffle-shop;
  - every column qualified with its CTE name;
  - lowercase SQL;
  - frozen dataclasses in the IR, and one error type per module boundary (`ParseError`, `Unsupported`,
    `OutputError`).
- **Architecture.**
  - Dependencies point one way: `cli → compiler/dbt_project → ir/expressions`. `sql` depends only on
    `compiler`'s result type.
  - `_Graph.upstream_of` is the only reader of connectors in the compiler (validation reads them for its
    own checks).
  - Status is set once in `compile_target`. Propagation runs in `compile_folder`, as the plan says.
  - `write_project` is the single writer that chooses `models/` or `incomplete/`.
  - Identifiers are validated once in `validate_mapping`; Jinja inside string literals is rejected once,
    in `translate`.
- **Over-building and under-building.** Each build sign was walked:
  - **Over-building:** there is no config option beyond `--overwrite`. There is no new dependency (YAML
    is written with `json.dumps`). There is no single-implementation interface.
  - **Under-building:** sibling callers are covered (the target-loaded-twice case was found and fixed).
    There is no log-and-continue path. Tests cover more than the sample: every join type, NULL keys,
    and the default router group.
- **Beyond the code.**
  - The output is consumed only by the validation harness and by reviewers; there is no deploy window
    and no cache.
  - The `ref()` names (lowercase target names) are the contract with any downstream dbt project, and
    nothing outside this repo uses them yet.
- **Size.** About 1,780 lines of source, 1,030 lines of tests and 120 lines of validation code. That is
  in line with the plan's 7 modules and its roughly 40-test target. The suite has 86 cases, because
  parametrized tests each count as a case.

## Risks

1. **Unverified PowerCenter semantics.** These are the **[U]** items: Joiner NULL keys, IIF with a NULL
   condition, `ERROR()` input defaults, and `number` mapped to `double`. If PowerCenter differs, the SQL
   is silently different. All of them are listed in the README.
2. **A faithful translation that is probably not what the author meant.** `EXP_MARK_FIRST_CUSTOMER`
   follows the documented variable order, so it gives `unique_customers = 0`. The README states this; a
   heuristic warning was rejected in the plan.
3. **Validation depends on pinned external versions.** It needs network access, jaffle-shop @5beb145b,
   and the dbt-utils git 1.3.0 workaround, because the reference requires dbt ≥ 2.0.
4. **Hayden should re-verify in a fresh shell.** The README sections on agent workflow and agent mistakes
   were written by Ahsoka from the briefs and the plan.

## Defect loop 1 (2026-11-01): `ERROR()` input defaults keep rows

Brief: `prompts/subagent-briefs.md`, "Brief 3". Nothing was committed.

**Evidence.** PowerCenter 10.5 docs, read by an online Claude with a browser (quotes in
`docs/00-recon.md` §8, "second pass"): I/O-port defaults handle NULL *input*; an `ERROR()` default
skips the row; an unconnected input is NULL and takes the default. Re-probed in this session with lxml:
188 Expression INPUT/OUTPUT ports, 186 blank and 2 `ERROR('Transformation Error')`
(`EXP_LOCATIONS.TAX_RATE`, `EXP_ORDER_ITEMS.ORDER_ID`); every other input-capable port in the export
is blank, except `EXP_ORDERS.ORDERED_AT` (`NULL`).

**Fix** (`src/pwc2dbt/compiler.py`).
- `_Inputs.skip_null_rows` returns one `<input> is not null` per input port with an `ERROR()`
  default, its upstream items as row dependencies (so the rule holds when no target needs the port),
  and one WARNING per port. An `ABORT()` default or an unconnected `ERROR()` input is a node problem
  that names the port.
- Expression, Filter, Router (every group, default included) and Aggregator (before `group by`) apply
  it through `_where`, which parenthesizes an existing condition before adding the rules.
- Sorter: `upstream_of` stops at a Sorter with any input default other than blank or `NULL`, and
  `_build_node` builds it with `_filter` (no Filter Condition, same rules).
- Joiner and Union: `row_default_port` makes the node INCOMPLETE. A pre-join filter needs a subquery
  per side; no export has the case, so INCOMPLETE is the smaller correct choice.
- `_Inputs.resolve` no longer warns on `ERROR()`; other non-NULL constants stay INCOMPLETE.

**Red to green.**

| Command | Exit code | Result |
|---|---|---|
| `pytest -q tests/test_transformations.py`, new tests on the old compiler | 1 | **RED**: 10 failed, 23 passed. Each execution case returned the NULL row `'b'` (e.g. `['a', 'b', 'c'] != ['a', 'c']`). |
| The same, after the fix | 0 | **GREEN**: 33 passed |
| `pytest -q` | 0 | 95 passed |
| `python -m pyflakes src tests validation/compare.py` | 0 | Clean |
| `python -m pwc2dbt --input tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml --output output --all-mappings --overwrite` | 2 | 14 complete, 2 incomplete, 0 errors, 16 warnings |
| `validation/run_validation.sh` | 0 | Reference seed PASS=6, build PASS=43 NO-OP=3; generated debug OK, parse 14 models, build PASS=14 ERROR=0 |

Tests: `test_input_port_default_values` had the case `("ERROR('bad')", "WARNING")`, which encoded the
defect. Hayden authorized replacing it. Each case now also asserts the message: `ERROR` gives a
WARNING "rows where X is NULL are skipped", `ABORT` gives INCOMPLETE, `0` gives INCOMPLETE. New:
`test_error_default_on_an_input_port_skips_rows_with_null_input` (Expression, Filter with an OR
condition, Sorter, Aggregator, Router default group; the target needs only `ID`, never `X`),
`test_unconnected_input_port_with_error_default_is_incomplete`,
`test_error_default_on_a_joiner_input_is_incomplete`, `test_error_default_on_a_union_input_is_incomplete`.

**Mutations: 10 of 10 caught**, each reverted: no `is not null` emitted; `is not null` to `is null`;
`_where` without parentheses; Expression row dependencies dropped; ABORT branch off; unconnected check
off; Sorter kept transparent; Aggregator `where` dropped; Joiner refusal off; Router `where` without the
rules.

**Regenerated output.** Two files change, one line each: `stg_locations.sql` gains
`where sq_raw_stores.tax_rate is not null` in `exp_locations`, and `stg_order_items.sql` gains
`where sq_raw_items.order_id is not null` in `exp_order_items`. Row counts are unchanged and were
checked, not assumed: `raw.raw_stores` 6 rows / 6 non-NULL `tax_rate`, `raw.raw_items` 90,900 / 90,900
non-NULL `order_id`; `gen.stg_locations` 6 and `gen.stg_order_items` 90,900, as before. The comparison
table is identical to the earlier run.

**Deviations.**
1. The Sorter rule uses any non-blank, non-`NULL` input default, not only `ERROR()`. One predicate
   makes the Sorter a node, and the ordinary rules then apply; this also closes the earlier "Sorter
   ports are not checked for input default values" finding.
2. `skip_null_rows` returns its problem as a fourth value, not as an exception, so the INCOMPLETE
   diagnostic names the port (the replaced test filters by port).

**Review findings left, with the argument for leaving each.**
- Source Qualifier port defaults are still not read. All 162 SQ ports in the export are blank, and
  the Designer is believed not to allow SQ defaults (unverified). Fixing it adds a check to a builder
  that no export needs.
- Union could filter per input branch instead of being INCOMPLETE. That is a second code path with no
  export behind it; INCOMPLETE is loud and easy to undo.
- `ERROR ('x')` with a space is not recognised and becomes INCOMPLETE as "replaces NULL input": loud,
  with a slightly wrong reason. The export never writes the space.
- `docs/01-plan.md` step 6(f) still describes the old WARNING rule. It is outside this loop's files;
  Hayden owns it.
