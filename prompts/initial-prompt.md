INITIAL MASTER PROMPT — Informatica PowerCenter to dbt Converter
1. Your mission
Act as a senior Product Engineer working on a four-hour technical take-home assignment for a Product Engineer interview.
Your task is to design, implement, test, and document a small but credible tool that converts Informatica PowerCenter mapping XML into equivalent dbt SQL models.
This is an engineering assessment. The quality of your reasoning, architecture, implementation, testing, and documentation matters just as much as the generated SQL.
Primary objective: Build a working, demonstrable, extensible converter that derives dbt models from actual PowerCenter XML metadata and transformation logic.
Do not hardcode the Jaffle Shop output. The converter must work from the XML structure and derive its output dynamically.
You are allowed to make reasonable assumptions and limit transformation coverage, provided those limitations are explicit, justified, and tested.
2. Mandatory engineering architecture: Hayden
Use the Hayden orchestration architecture:
https://hayden.boyanbalevengineering.com/
This is a mandatory architectural requirement, not optional inspiration.
Before starting the implementation:

1. Read the full Hayden skill and its instructions.
2. If it is not already available in Claude Code, retrieve its complete Markdown skill definition from the official website and install it as instructed.
3. Make it available as `.claude/commands/hayden.md`.
4. Invoke the `/hayden` command for this assignment.

Do not invent or simplify the architecture.
Required agent responsibilities
Hayden — Orchestrator

* Performs repository and domain reconnaissance.
* Never writes production code.
* Identifies the actual problem and relevant constraints.
* Produces `00-recon.md`.
* Writes the briefs for Hashi and Ahsoka.
* Reviews the planning handoff.
* Controls scope and implementation.
* Performs final verification in a fresh shell.
* Produces the final engineering report.

Hashi — Planner and adversarial critic

* Re-reads the relevant code and verifies Hayden's findings.
* Investigates the PowerCenter XML and dbt conversion problem.
* Evaluates two or three implementation approaches.
* Selects the smallest complete architecture.
* Writes an implementation plan.
* Critically attacks that plan, looking for missing cases, incorrect assumptions, and unnecessary complexity.
* Produces `01-plan.md` with a clear READY, RISKS, or BLOCKED verdict.
* Never writes production code.

Ahsoka — Implementer and reviewer

* Independently verifies the plan against the actual project and XML.
* Writes failing proof tests before implementing the corresponding functionality.
* Is the only agent permitted to edit production code.
* Implements the agreed plan.
* Reviews her own changes from a fresh critical perspective.
* Mutation-tests important logic.
* Fixes issues discovered during review.
* Produces `02-build.md` with implementation evidence.

Agent orchestration rules

* Run Hashi and Ahsoka strictly sequentially.
* Never run both agents in parallel.
* Avoid introducing additional agents.
* Every agent must re-read the actual relevant source code rather than blindly trusting another agent's summary.
* Hayden must never write production code.
* Hashi must never write production code.
* Ahsoka owns implementation and its review.
* Ahsoka can deviate from the plan only when she records the reason.
* Hayden can allow at most one planning revision.
* Final implementation defects can be returned to Ahsoka at most twice.
* If Claude Code cannot actually spawn the required subagents, explicitly report the limitation rather than pretending that separate agents were used.

Apply Hayden's 15 laws and five-point finding bar throughout the task.
In particular:

* Read before writing.
* Prefer the smallest complete implementation.
* Avoid abstractions for hypothetical future use cases.
* Enforce important invariants at their source.
* Never silently produce incorrect data.
* Do not claim something works unless it was verified during this session.

3. Assignment requirements
The official assignment is:
Take-home exercise: Informatica PowerCenter to dbt converter.
Requirements:

1. Read and understand the supplied PowerCenter XML.
2. Build a converter in a language of your choice.
3. Generate equivalent dbt SQL models from the XML.
4. Include automated tests.
5. Compare the generated models against the public Jaffle Shop reference.
6. Do not hardcode Jaffle Shop output.
7. Document assumptions, unsupported functionality, limitations, and potential errors.

Official references:
PowerCenter XML:
https://github.com/infinitelambda/flowline-pwc-demo
XML file:
https://raw.githubusercontent.com/infinitelambda/flowline-pwc-demo/main/FLOWLINE_DEMO_JAFFLESHOP.xml
dbt reference:
https://github.com/dbt-labs/jaffle_shop_duckdb
PowerCenter Designer Guide:
https://docs.informatica.com/data-integration/powercenter/10-5/designer-guide/mappings/mappings-overview.html
PowerCenter Transformation Guide:
https://docs.informatica.com/data-integration/powercenter/10-5/transformation-guide/preface.html
dbt documentation:
https://docs.getdbt.com/docs/build/models
Also use the two locally provided PDF reference documents if they are present in the working environment:

* `PC_1050_DesignerGuide_en.pdf`
* `PC_105_TransformationGuide_en.pdf`

These are important domain references. Use them to verify PowerCenter transformation semantics rather than relying exclusively on intuition or AI-generated explanations.
4. Initial reconnaissance — do not code immediately
Hayden must investigate the task and actual repository before producing the implementation brief.
A. Understand the sample XML
Download and inspect the actual XML.
Identify:

* Repository and folder metadata.
* Source definitions and source fields.
* Target definitions and target fields.
* Mapping definitions.
* Transformation definitions.
* Transformation instances.
* Port definitions and expressions.
* Connectors and their relationships.
* Transformation attributes.
* Mapping-to-workflow relationships.
* Multiple source instances and multiple target outputs.

Important: The XML is a complete repository export containing three mappings:

* `m_FL_JS_STAGING_FULL`
* `m_FL_JS_MARTS_CORE`
* `m_FL_JS_MARTS_ENRICHED`

It also contains a workflow that runs these mappings sequentially.
Understand how the mappings depend on each other.
Do not assume that one mapping equals one dbt model.
Investigate whether the correct output architecture is one dbt model per target, with references between generated models where appropriate.
B. Identify actual transformation types
Inspect the transformations present in the XML.
At minimum, investigate:

* Source Qualifier
* Expression
* Filter
* Router
* Sorter
* Joiner
* Aggregator

Identify their actual configuration, port definitions, and connectors.
Do not assume that every transformation can be represented by a simple SQL clause.
C. Understand the dbt reference project
Clone:
`https://github.com/dbt-labs/jaffle_shop_duckdb`
Investigate:

* Existing project structure.
* Model naming conventions.
* Source and seed configuration.
* SQL dialect.
* Model dependencies.
* Existing model SQL.
* How the project is executed.
* How results can be validated.

Use the reference project as a validation target, not as a source of hardcoded converter output.
D. Reconnaissance report
Create:
`docs/00-recon.md`
Include:

* Verified facts about the XML.
* Mapping dependency overview.
* Transformation inventory.
* Proposed conversion scope.
* Existing project conventions.
* Unverified assumptions.
* Risks and important edge cases.

Clearly distinguish verified information from assumptions.
Do not start production implementation until this investigation is complete.
5. Conversion architecture
Build a small, modular converter.
Prefer a Python implementation unless reconnaissance reveals a compelling reason to choose another language.
Avoid building an unnecessarily complex framework.
The converter should conceptually follow this pipeline:
`PowerCenter XML → XML Parser → Mapping Graph → Transformation Compiler → SQL Generator → dbt Models`
Stage 1: XML Parser
Implement a parser that reads actual PowerCenter repository XML.
Requirements:

* Handle the declared Windows-1252 encoding.
* Handle the PowerCenter DTD declaration safely.
* Do not allow external entity resolution.
* Parse repository, folder, source, target, mapping, transformation, instance, connector, port, and attribute metadata.
* Preserve the relationship between transformation definitions and their instances.
* Correctly resolve source and target instances.
* Support multiple mappings in one repository export.

Do not use brittle line-based XML parsing or hardcoded mapping names.
Stage 2: Intermediate Representation
Create a clean intermediate representation (IR) of the mapping.
The IR should represent:

* Sources.
* Targets.
* Transformations.
* Transformation instances.
* Input and output ports.
* Port expressions.
* Connectors.
* Transformation attributes.
* Graph dependencies.

Represent the data flow as a directed graph.
The IR should be independent of the SQL-generation logic.
This separation is important: parsing XML and generating SQL should not be one large function.
Validate the graph before compilation.
Detect:

* Missing references.
* Invalid connector endpoints.
* Missing required ports.
* Unresolvable dependencies.
* Cycles.
* Unsupported transformation types.

Where appropriate, produce actionable error messages containing the mapping and transformation names.
Stage 3: Transformation compiler
Implement conversion rules for the transformation types actually present in the sample.
Prioritize these transformations:
Source Qualifier

* Resolve the associated source instance.
* Map source fields to SQL columns.
* Handle source filters.
* Handle Select Distinct.
* Investigate user-defined joins and SQL overrides.
* Preserve source table identity.

Expression

* Convert output-port expressions into SQL expressions.
* Resolve input-port references through connectors.
* Preserve field aliases.
* Handle pass-through ports.
* Handle nested expressions and local variables where feasible.
* Implement a small, explicit function translation layer.

Do not perform naive string substitutions on arbitrary Informatica expressions.
Filter

* Convert filter conditions into SQL WHERE clauses.
* Preserve row-filtering semantics.

Router

* Handle named output groups and their conditions.
* Handle default groups.
* Preserve branch-specific filtering.
* Ensure that multiple branches do not accidentally duplicate or lose rows.

Sorter

* Extract sort keys and directions.
* Preserve sorting where it affects meaningful SQL behavior.
* Do not introduce ORDER BY into every generated model unnecessarily.
* Investigate how Sorter behavior relates to Aggregator and Joiner transformations.

Joiner

* Identify both input pipelines.
* Extract join conditions.
* Support the join types found in the sample.
* Preserve normal, master outer, detail outer, and full outer semantics where applicable.
* Correctly resolve fields from both input branches.
* Handle chained Joiner transformations.

Aggregator

* Extract group-by ports.
* Extract aggregate expressions.
* Support common aggregate functions such as SUM, COUNT, MIN, MAX and AVG.
* Generate the appropriate GROUP BY clause.
* Preserve non-aggregate grouping expressions.
* Handle multiple aggregate outputs.

If the XML contains additional transformation types, identify them and evaluate whether implementing them is worthwhile within the time limit.
Stage 4: Informatica expression translation
Expression conversion is one of the most important parts of this assignment.
Implement a focused translation layer for the expressions actually used in the sample.
Investigate functions such as:

* IIF
* DECODE
* NVL
* ISNULL
* TO_DATE
* TO_CHAR
* DATE_DIFF
* ADD_TO_DATE
* SUBSTR
* INSTR
* CONCAT
* Aggregate functions

Only implement translations that can be justified by PowerCenter semantics and the target SQL dialect.
Important rules:

* Do not assume Informatica expressions are equivalent to Oracle SQL expressions.
* Preserve null-handling behavior.
* Pay attention to data types.
* Pay attention to date formatting and casting.
* Do not silently change numeric precision or scale.
* Handle nested functions.
* Explicitly identify expressions that cannot be translated safely.

For unsupported expressions, fail with a clear diagnostic or mark the output as incomplete.
Never silently replace unsupported logic with NULL, a constant, or an arbitrary SQL expression.
Stage 5: SQL generation
Generate readable SQL that follows dbt conventions.
Requirements:

* Use meaningful aliases.
* Use consistent formatting.
* Preserve target field names.
* Generate valid SQL for the chosen dialect.
* Generate correct FROM, JOIN, WHERE, GROUP BY and SELECT clauses.
* Preserve transformations in their correct logical order.
* Avoid unnecessary subqueries and CTEs.
* Use CTEs when they improve clarity or are needed to preserve graph semantics.

Where possible, use dbt `source()` and `ref()` appropriately.
Raw input tables should be represented as dbt sources when appropriate.
Generated upstream models should be referenced through `ref()`.
Do not simply use hardcoded table names when a dbt dependency should exist.
Stage 6: dbt output
Generate a usable dbt project or integrate generated models into the cloned reference project.
Suggested output:

```
output/
  dbt_project.yml
  models/
    staging/
      stg_customers.sql
      stg_orders.sql
      stg_products.sql
      ...
    marts/
      customers.sql
      orders.sql
      order_items.sql
      ...
    schema.yml
```

The actual structure should be determined by the mapping graph and generated model dependencies, not by this example.
Where feasible:

* Generate model documentation.
* Generate basic schema tests.
* Preserve model dependencies.
* Ensure dbt model names are valid.
* Avoid creating duplicate model definitions.

6. Core engineering requirements
No hardcoding
This is non-negotiable.
Do not:

* Hardcode Jaffle Shop SQL.
* Hardcode generated target values.
* Hardcode expected expressions based on mapping names.
* Use a special-case branch that generates the reference project's output.
* Write a converter that only works for the supplied XML.

The XML may be used as a fixture in tests, but conversion logic must operate on parsed metadata.
Use generic transformation handlers.
Correctness over apparent coverage
A smaller converter that produces correct SQL for supported transformations is better than a converter that claims to support everything but generates incorrect SQL.
Maintain an explicit list of supported and unsupported features.
Diagnostics
The converter should communicate:

* Which mapping is being processed.
* Which transformations are being compiled.
* Which expressions are unsupported.
* Which graph dependencies cannot be resolved.
* Whether generated SQL is complete.
* Which assumptions were applied.

Warnings must never be confused with successful conversion.
Determinism
Given the same XML and configuration, the converter should generate identical output.
Avoid random naming, unstable ordering, or unnecessary timestamps in generated SQL.
Maintainability
Keep the implementation small and readable.
Separate responsibilities:

* XML parsing.
* IR construction.
* Graph validation.
* Expression translation.
* Transformation compilation.
* SQL rendering.
* dbt file generation.
* CLI.

Avoid introducing excessive abstractions.
7. Testing strategy
Testing is a major evaluation criterion.
Use automated tests, preferably pytest if Python is selected.
Create tests at several levels.
A. XML parser tests
Verify:

* Correct XML loading.
* Correct source extraction.
* Correct target extraction.
* Correct mapping extraction.
* Correct transformation extraction.
* Correct port extraction.
* Correct connector resolution.
* Correct instance resolution.
* Handling of missing or invalid XML references.

B. Transformation tests
Create isolated test fixtures for:

* Source Qualifier.
* Expression.
* Filter.
* Router.
* Sorter.
* Joiner.
* Aggregator.

Test actual conversion semantics, not merely whether the generated SQL contains a particular keyword.
Include null-handling and expression edge cases where relevant.
C. Graph tests
Test:

* Linear pipelines.
* Multiple input pipelines.
* Chained transformations.
* Multiple targets.
* Branching.
* Cyclic dependencies.
* Invalid connector references.
* Multiple source instances.

D. Integration tests
Run the converter against the actual supplied XML.
Verify that:

* All three mappings are discovered.
* Their transformation graphs are constructed.
* Generated outputs correspond to actual targets.
* Dependencies between mappings are preserved.
* Unsupported logic is reported.
* No Jaffle-specific output has been hardcoded.

E. dbt validation
Use the DuckDB Jaffle Shop reference project where practical.
Run:

```
dbt debug
dbt parse
dbt compile
dbt build
```

Only report commands as successful if they were actually executed successfully.
If the generated project requires modifications to the reference project or a separate test environment, document that clearly.
Do not confuse successful SQL compilation with proven semantic equivalence.
F. Regression testing
Save generated SQL as test fixtures or snapshots where useful.
Verify deterministic output.
Test at least one meaningful negative case, where the converter must reject or flag unsupported logic instead of generating misleading SQL.
8. Validation against Jaffle Shop
Use the reference project to evaluate the converter's correctness.
Reference:
https://github.com/dbt-labs/jaffle_shop_duckdb
The objective is to compare the generated SQL with the intended transformation logic in the reference models.
Do not expect the SQL to be textually identical.
Compare:

* Output columns.
* Column aliases.
* Source relationships.
* Join conditions.
* Filtering behavior.
* Calculated expressions.
* Aggregation logic.
* Grouping.
* Null handling.
* Resulting data.

Where feasible, execute both implementations against equivalent input data and compare their output rows.
If a full equivalence check is impractical, perform a documented partial comparison.
Clearly identify differences caused by:

* Unsupported PowerCenter functionality.
* SQL dialect differences.
* Assumptions.
* Differences between PowerCenter execution semantics and dbt execution semantics.

9. CLI requirements
Create a simple command-line interface.
Example:

```
python -m pwc2dbt \
  --input FLOWLINE_DEMO_JAFFLESHOP.xml \
  --output output/ \
  --mapping m_FL_JS_STAGING_FULL
```

Also support:

```
python -m pwc2dbt \
  --input FLOWLINE_DEMO_JAFFLESHOP.xml \
  --output output/ \
  --all-mappings
```

These are proposed interfaces; adapt them to the selected implementation.
The CLI should:

* Accept an XML file.
* Allow selection of an individual mapping.
* Support processing all mappings.
* Specify an output directory.
* Display progress and diagnostics.
* Return meaningful exit codes.
* Avoid overwriting unrelated files without clear intent.

10. Suggested repository structure
Choose the smallest structure that fits the implementation.
A possible structure:

```
pwc-to-dbt/
│
├── .claude/
│   └── commands/
│       └── hayden.md
│
├── docs/
│   ├── 00-recon.md
│   ├── 01-plan.md
│   ├── 02-build.md
│   ├── architecture.md
│   ├── conversion-rules.md
│   └── limitations.md
│
├── src/
│   └── pwc2dbt/
│       ├── __init__.py
│       ├── parser.py
│       ├── models.py
│       ├── graph.py
│       ├── expressions.py
│       ├── transformations.py
│       ├── compiler.py
│       ├── renderer.py
│       ├── dbt_generator.py
│       └── cli.py
│
├── tests/
│   ├── fixtures/
│   ├── test_parser.py
│   ├── test_graph.py
│   ├── test_expressions.py
│   ├── test_transformations.py
│   ├── test_compiler.py
│   └── test_integration.py
│
├── prompts/
│   └── initial-prompt.md
│
├── output/
│
├── README.md
├── pyproject.toml
└── .gitignore
```

Do not force this exact structure if reconnaissance suggests a smaller, cleaner alternative.
11. Time management — four-hour exercise
Treat the assignment as a strict four-hour engineering exercise.
Prioritize:
Phase 1 — Reconnaissance (approximately 35 minutes)

* Read the XML.
* Understand the mapping structure.
* Inspect dbt reference models.
* Read relevant PowerCenter documentation.
* Produce the recon report.

Phase 2 — Planning (approximately 20 minutes)

* Hashi develops and attacks the implementation plan.
* Hayden reviews the handoff.
* Agree on a realistic scope.

Phase 3 — Implementation (approximately 110 minutes)

* Build XML parsing.
* Build the intermediate representation.
* Implement graph handling.
* Implement the highest-value transformation handlers.
* Implement SQL generation.
* Build the CLI.

Phase 4 — Testing and validation (approximately 35 minutes)

* Execute automated tests.
* Run the converter against the sample.
* Validate generated SQL.
* Run dbt commands where possible.
* Fix critical defects.

Phase 5 — Documentation and final review (approximately 20 minutes)

* Write README.
* Document limitations.
* Record agent mistakes and how they were caught.
* Verify final repository state.

These are planning allocations, not permission to skip important correctness checks.
If implementation takes longer than expected, reduce scope rather than sacrificing correctness.
12. README — important interview deliverable
The README must directly address every submission requirement.
Include:
Project overview
Explain what the converter does and its architecture.
Installation
Explain dependencies and setup.
Running the converter
Provide copy-pasteable commands.
Running tests
Provide exact commands.
Example output
Show an actual generated SQL model.
Do not use illustrative SQL and imply that it was generated by the implementation.
Assumptions
Explain the assumptions made about PowerCenter metadata, transformations, SQL dialect, and dbt model dependencies.
Unsupported functionality
List the transformations and expression features that are not supported.
Where the converter can produce incorrect output
Explain the highest-risk areas and why.
Coding agent mistakes
Document at least one genuine mistake made by the coding agent, how it was discovered, and how it was corrected.
Do not invent agent mistakes. If no meaningful mistake occurred, document a real issue or incorrect assumption caught during validation.
Agent workflow
Describe how Hayden, Hashi, and Ahsoka were used.
Include the prompts given to the coding agent, starting with this initial prompt.
Preserve the available agent conversation export in the repository if possible.
13. Quality gates
Before declaring the assignment complete, verify the following.
Functionality

* XML is read dynamically.
* Three mappings are discovered.
* Mapping graphs are constructed.
* Source and target relationships are preserved.
* Supported transformations are translated.
* Unsupported transformations are reported.
* SQL is generated dynamically.
* dbt output has valid model dependencies.
* CLI works.

Testing

* Parser tests pass.
* Graph tests pass.
* Expression tests pass.
* Transformation tests pass.
* Integration tests pass.
* Negative tests exist.
* SQL validation has been attempted.
* dbt validation has been attempted.

Engineering

* No hardcoded Jaffle Shop SQL.
* No silent unsupported-expression fallbacks.
* No unnecessary abstractions.
* Generated SQL is readable.
* Errors are actionable.
* Generated output is deterministic.

Submission

* README is complete.
* Assumptions are documented.
* Limitations are documented.
* Potential incorrect-output scenarios are documented.
* Actual coding-agent mistakes or validation discoveries are documented.
* Initial prompt and subsequent prompts are preserved.
* Hayden workflow is documented.

14. Final review — Hayden
After Ahsoka completes the implementation:
Hayden must conduct the final review without editing production code.
Use a fresh shell.
Run the relevant commands again:

* Automated tests.
* Type checking, if applicable.
* Linting, if configured.
* CLI smoke test.
* dbt validation, if available.

Inspect:

```
git status --short
git diff --stat
git diff --check
git diff --name-only
```

Check for:

* Accidental changes.
* Debugging statements.
* Disabled tests.
* Weakened assertions.
* Unnecessary dependencies.
* Incorrect generated SQL.
* Missing documentation.
* Unsupported functionality presented as supported.

Any finding must satisfy Hayden's five-point bar:

1. Precisely identify the problem.
2. Explain who is affected and how often.
3. Identify the smallest useful fix.
4. Explain the complexity introduced by that fix.
5. Consider the honest argument for leaving it unchanged.

Only report verified results.
Do not claim that the implementation is production-ready or semantically equivalent unless the evidence supports that claim.
15. Final response
When the assignment is complete, provide a concise final report containing:

1. What was built.
2. How the converter works.
3. Which transformations are supported.
4. Which transformations remain unsupported.
5. Tests executed and their actual results.
6. dbt validation results.
7. Known limitations.
8. Files created.
9. Exact commands for running the converter.
10. Any remaining issues that should be addressed before submission.

Also provide a short assessment of the solution from the perspective of a Product Engineer interviewer.
Identify:

* Strong engineering decisions.
* Weaknesses.
* Potential interview questions.
* Areas where I should understand the implementation particularly well.

Final instruction:
Optimize for a thoughtful, correct, well-tested four-hour take-home solution.
Do not optimize for lines of code, number of features, or an impressive-looking architecture diagram.
I need a solution that I can personally understand, explain, defend, and confidently submit in an interview.
Start with Hayden reconnaissance. Do not write production code until the reconnaissance and planning phases are complete.
