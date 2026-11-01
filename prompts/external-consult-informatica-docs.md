# Prompt for an online Claude: verify Informatica PowerCenter 10.5 semantics

You have web access. I need **exact, quoted evidence** from the official Informatica PowerCenter 10.5 documentation on docs.informatica.com. I am building a converter from PowerCenter mapping XML to dbt SQL in the DuckDB dialect. Each answer below decides how one construct is translated, and a wrong answer produces silently wrong data. So:

- **Quote, don't paraphrase.** For every answer, give the exact sentence or sentences from the doc page and the full URL of that page.
- **Say "NOT FOUND"** if the docs don't state the answer explicitly. Do not answer from memory or general SQL knowledge.
- **Version.** Prefer the 10.5 pages (`/powercenter/10-5/`). If only another 10.x version states it, quote that and name the version.
- If a page won't load, say so and try the same page under another 10.x version path.

Starting points:
- Designer Guide: https://docs.informatica.com/data-integration/powercenter/10-5/designer-guide/mappings/mappings-overview.html
- Transformation Guide: https://docs.informatica.com/data-integration/powercenter/10-5/transformation-guide/preface.html
- Transformation Language Reference, IIF: https://docs.informatica.com/data-integration/powercenter/10-5/transformation-language-reference/functions/iif.html

## Questions

**Q1. IIF with a NULL condition.**
In `IIF(condition, value1, value2)`, what does PowerCenter return when `condition` evaluates to NULL? For example, `IIF(TAX_RATE >= 0.15, 'EMEA', 'AMER')` when TAX_RATE is NULL. Is it value2 (as in SQL `CASE WHEN`), NULL, or something else?
- Check the IIF page.
- Check the Transformation Language Reference sections on NULL ("Null Values in Boolean Expressions", "Null Values in Comparison Expressions", the NULL constant).

**Q2. Unconnected target columns.**
When a target definition column has no incoming link in a mapping, what does the Integration Service write to it: NULL, the column's database default, or something else?
- Check the Designer Guide (mappings, linking ports, targets).
- Check the Workflow Basics or Advanced Workflow Guide sections on target loading, if needed.

**Q3. Default values on INPUT/OUTPUT ports, and how they appear in the XML export.**
In the exported XML, every `TRANSFORMFIELD` has one `DEFAULTVALUE` attribute. On INPUT/OUTPUT ports of Expression transformations, I see `DEFAULTVALUE="ERROR('Transformation Error')"`.
- (a) Quote the docs on the system default values for input, output and input/output ports: for null input, and for transformation errors.
- (b) Does the docs say an input/output port's default value applies to **null input** or to **output errors**? I found this excerpt on "Default Values for Ports": "The output default value of input/output ports is always ERROR('Transformation Error')". Please confirm it and quote its surrounding context.
- (c) If a user sets an input/output port's default to `ERROR('...')`, does the Integration Service skip rows whose input is NULL? Check "Skip Null Records" and "User-Defined Default Input Values".
- (d) Is there any doc statement about how these defaults are stored in the repository or XML export? For example, whether DEFAULTVALUE on an I/O port holds the null-input default. Answer NOT FOUND if absent.

**Q4. Variable port evaluation and initial values.** This is a confirmation; I already have excerpts.
- Quote the "Port Order" page on the evaluation order of input, variable and output ports.
- Quote the statement on initial values of variable ports: 0 for numeric, empty string for string, and the date default.
- Given:
  - `v_PREV = CUSTOMER_ID` (listed first)
  - `v_SEQ = IIF(v_PREV = CUSTOMER_ID, v_SEQ + 1, 1)` (listed second)

  does the doc text imply that `v_PREV` already holds the *current* row's value when `v_SEQ` is evaluated? Answer only from the quoted rules, and say if the docs are ambiguous.

**Q5. Union transformation port matching.**
Do the docs say how the Union transformation matches input-group ports to output-group ports: by position, by name, or by an explicit field mapping? Quote it. Also quote whether Union removes duplicate rows.

## Response format

Reply in exactly this shape, so it can be pasted back to the engineering agent:

```
Q1: <answer in one line> | VERIFIED or NOT FOUND
  Quote: "<exact text>"
  URL: <url>   (version: 10.5 / other)
Q2: ...
Q3a: ...
Q3b: ...
Q3c: ...
Q3d: ...
Q4: ...
Q5: ...
Notes: <pages that failed to load, contradictions between versions, anything surprising>
```
