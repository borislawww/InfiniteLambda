# Subagent briefs written by Hayden

Each brief below was sent verbatim. It was followed by the role block, the Law, the Bar, the Decisions procedure and Field sense, all copied verbatim from `.claude/commands/hayden.md`. Those parts are not repeated here.

---

## Brief 1: Hashi (planning), sent after docs/00-recon.md

OBJECTIVE
Plan a converter from Informatica PowerCenter repository XML to dbt SQL models.
- The task, verbatim, is `prompts/initial-prompt.md` (the master prompt). Sections 3, 5, 6, 7, 8, 9, 10, 12 and 13 are binding requirements.
- Why it matters: this is an interview take-home. The interviewer judges correctness, tests, honesty of limitations and design restraint. Today the repo has no code at all.

DONE MEANS
- `docs/01-plan.md` exists with sections 1–8 of your role, an attack log and a verdict.
- The plan can be built by ONE implementer in about 110 minutes, followed by about 35 minutes of validation. Size the scope to that, and say what you cut.
- Every requirement in master prompt §13 "Quality gates" is mapped to a plan step, or explicitly cut with a reason.

READ FIRST (SP = /tmp/claude-0/-home-user-InfiniteLambda/abff589f-43e2-535b-816e-ab7ac6f63c6c/scratchpad)
- `docs/00-recon.md`. §5 lists traps, §7 locked decisions D1–D7, and §8 PowerCenter semantics checked against docs.informatica.com excerpts.
- `prompts/initial-prompt.md`.
- The XML, which is not yet in the repo: `SP/xml/FLOWLINE_DEMO_JAFFLESHOP.xml`.
- Hayden's inspection aid: `SP/dump.py` and its output `SP/dump.txt`. Treat it as a convenience only; verify anything load-bearing against the XML itself.
- Reference project matching the XML domain: `SP/jaffle/jaffle-shop` (models/, seeds/jaffle-data/, macros/).
- Mandated but schema-incompatible reference: `SP/jaffle/jaffle_shop_duckdb`.

UNVERIFIED (check these yourself, and record verified or still-unverified in the plan)
- U1: Union port mapping. Do input-group ports map to output ports by position or by name? Check UN_REGIONS and UN_ORDER_BANDS ports and connectors against recon §8 S5.
- U2: Which targets in all 3 mappings have target columns with no incoming connector?
- U3: Joiner conditions put the master port on either side. Confirm, for every joiner, which side is master using the `/MASTER` port type.
- U4: DuckDB division semantics, and how a Python test can execute generated SQL. `duckdb` from PyPI is installable; PyPI is reachable.
- U5: Can `dbt-duckdb` (PyPI) build a generated project in this sandbox, and can jaffle-shop's reference models run on dbt 1.x?
  - jaffle-shop needs dbt ≥2.0 and `dbt_utils`.
  - Check whether hub.getdbt.com or github.com/dbt-labs/dbt-utils is reachable. GitHub cloning works.
- U6: The remaining [U] semantics in recon §8 (S2, S11, S14, S15). docs.informatica.com is NOT reachable from here. The plan must make the converter's behavior explicit and testable for each, and the README must list each as an assumption.

LOCKED DECISIONS: D1–D7 in `docs/00-recon.md` §7. Challenge one only with evidence from the XML or the references.

DECISIONS THE PLAN MUST MAKE (recommend one, with reasons)
- How a target's upstream subgraph becomes one model's SQL. Shared upstream instances, such as SQ_STG_ORDERS feeding several branches, must not be double-counted or mis-joined.
- How port names stay unique inside SQL when a joiner merges two pipelines with overlapping names.
- The incomplete-model policy (recon D5): what gets written to disk, what the CLI prints, and the exit code.
- Target type casting policy. High precision is OFF; target columns are NUMBER(p,s), DATE and VARCHAR2.
- Unconnected target columns.
- `$$RUN_DATE`: for example, a dbt var with the XML default.
- The expression translator design. It must parse into a tree; no regex substitution on arbitrary text.
- The validation design: dbt build of the generated project on jaffle-shop seeds, plus a row or aggregate comparison against reference outputs, with known divergences explained.
- The module list. Prefer fewer modules than master prompt §10 if responsibilities stay separate.
- The test list per master prompt §7, with fixture strategy: small hand-written XML fixtures plus the real XML.

CONSTRAINTS AND NON-GOALS
- You write NO production code and NO tests. Read-only probes in the scratchpad are fine.
- No Jaffle-specific logic, ever: no mapping, target or port names in converter code.
- Non-goals: workflow/session execution, Command tasks, parameter files, mapplets, Lookup, Update Strategy, incremental models.

TRAPS (beyond recon §5)
- Transformation names repeat across mappings (JNR_ADD_PRODUCTS and AGG_ITEMS_BY_ORDER exist in both CORE and ENRICHED). Scope every lookup by mapping.
- Router output ports are renamed copies (LOCATION_ID1 with REF_FIELD=LOCATION_ID), one set per group.
- Expression INPUT/OUTPUT ports without a real expression are pass-through.
- Some computed ports go nowhere, for example AGG_ITEMS_BY_ORDER.ITEMS_COUNT in CORE. Dead ports are not errors.
- Source Qualifier ports take their datatypes from the SQ, not the source definition.
- The CUSTOMER_360 target receives some columns from the main pipeline only.

STOP CONDITIONS
- If XML evidence contradicts a locked decision, return BLOCKED with one question and a recommended default.
- If the scope cannot fit the time budget, cut. Say what was cut and why. Do not stop.

RETURN
Write `docs/01-plan.md`. Reply with only the handoff (role format, at most 30 lines).

---

## Brief 2: Ahsoka (build and review), sent after docs/01-plan.md (verdict READY WITH RISKS)

OBJECTIVE
Build the converter that `docs/01-plan.md` specifies, then review it as the person who must sign it.
- The task, verbatim, is `prompts/initial-prompt.md`.
- Why it matters: an interview take-home. The repo has only docs today, and the interviewer judges correct SQL, honest limits and real tests.

DONE MEANS
- Every plan step 1–15 is done, or logged as a Deviation with a reason. That covers the code, the committed `output/`, `validation/` with real results, the top-level `README.md` and `docs/02-build.md`.
- The plan §5 proof test was seen RED before the code that satisfies it, and is GREEN after.
- The full pytest suite is green. `python -m pwc2dbt ... --all-mappings` exits with the code the plan predicts.
- dbt debug, parse, compile and build were actually run on the generated project. Report the real results, including failures.
- Mutation testing: at most 5 lines carrying the change, each mutated, then the targeted test run red, then reverted. Report N of M.

READ FIRST (SP = /tmp/claude-0/-home-user-InfiniteLambda/abff589f-43e2-535b-816e-ab7ac6f63c6c/scratchpad)
- `docs/01-plan.md`, all of it. It is your spec.
- `docs/00-recon.md`, especially §8, PowerCenter semantics with doc sources.
- `prompts/initial-prompt.md`, sections 7, 8, 9, 12 and 13.
- The real XML: `SP/xml/FLOWLINE_DEMO_JAFFLESHOP.xml`. Copy it to `tests/fixtures/` (plan step 1).
- The reference project: `SP/jaffle/jaffle-shop`. Hashi's probes, including the dbt-utils workaround: `SP/hashi/`.

KNOWN FACTS (verified by Hayden and Hashi; re-verify anything load-bearing)
- Recon §5.2 was WRONG: CUSTOMER_360 has 19 of 19 columns connected. Hayden truncated a dump line at 400 chars and read it as complete.
- Recon §5.3 was partly wrong. Only `v_ORDER_SEQUENCE` is stateful. `EXP_MARK_FIRST_CUSTOMER` is pure under top-to-bottom variable evaluation.
- Both corrections are real agent mistakes. The README must record them under "Coding agent mistakes", with how each was caught. Add any genuine mistakes of your own; never invent one.
- dbt-core 1.12.6 with dbt-duckdb 1.11.1 installs from PyPI. GitHub cloning works. docs.informatica.com is blocked; do not retry it.

LOCKED DECISIONS
- D1–D7 in recon §7, plus every decision in plan §2–§4.
- The user explicitly wants a top-level `README.md`. Do NOT create any other README files; the user removed one and called it not useful.
- Plan step 13's `validation/README.md` is therefore replaced: put the validation results in a "Validation against Jaffle Shop" section of the top-level README, and in `docs/02-build.md`. Log this as a Deviation.

CONSTRAINTS AND NON-GOALS
- You are the only agent that edits code. Stay inside plan §3 scope; this is a Deep task, so a file outside scope means stop and report.
- Create the venv at `.venv/` in the repo (gitignored). Keep scratch work in SP, not the repo.
- No Jaffle-specific names in `src/` (no mapping, target or port names). Grep for them before you finish.
- No silent fallbacks. An unsupported thing becomes INCOMPLETE with a diagnostic, or a fatal ERROR.
- Do not commit or push. Hayden does that after the close.

TRAPS
- `/` in DuckDB is always a double. An overflowing DECIMAL cast raises an error at runtime, not compile time; check casts at target precision against the real seed data in validation.
- The README example SQL must be copied from the real `output/` file, never written by hand.
- Validation divergences against jaffle-shop are expected: swapped wiring, IS_PERISHABLE 'Y'/'1' vs 'True'/'False', dollars vs cents, the date truncation type. Explain each one with evidence; never "fix" the converter to match the reference.
- Tests must assert values produced by executing the SQL, not keywords in the text.

STOP CONDITIONS
- The same check is still red after two genuine attempts: stop and report both attempts.
- A file is needed outside plan §3 scope (Deep): stop and report.
- The plan contradicts the XML: take the minimal correction and log a Deviation, unless it changes a locked decision, in which case stop.

RETURN
Write `docs/02-build.md`. Reply with only the handoff (role format, at most 30 lines).
