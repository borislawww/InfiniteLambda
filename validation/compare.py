"""Compare generated models (schema gen) with jaffle-shop models of the same name (schema main).

For the columns both sides share it reports row counts, rows only on one side
(EXCEPT ALL, numerics rounded to 2 dp, dates as timestamps) and which single
columns differ as value multisets. Prints a Markdown table.
Usage: python validation/compare.py path/to/jaffle.duckdb
"""

import sys

import duckdb

NUMERIC = ("DECIMAL", "DOUBLE", "FLOAT", "INTEGER", "BIGINT", "HUGEINT", "SMALLINT", "TINYINT", "BOOLEAN")


def columns(con, schema: str, table: str) -> dict[str, str]:
    rows = con.execute(
        "select column_name, data_type from information_schema.columns where table_schema = ? and table_name = ? "
        "order by ordinal_position", [schema, table]).fetchall()
    return dict(rows)


def normalized(column: str, data_type: str) -> str:
    if data_type == "BOOLEAN":
        column = f"cast({column} as integer)"
    if data_type.startswith(NUMERIC):
        return f"cast(round(cast({column} as double), 2) as varchar)"
    if data_type in ("DATE", "TIMESTAMP"):
        return f"cast(cast({column} as timestamp) as varchar)"
    return f"cast({column} as varchar)"


def except_count(con, left: str, right: str) -> int:
    return con.execute(f"select count(*) from ({left} except all {right})").fetchone()[0]


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    generated = [t for (t,) in con.execute(
        "select table_name from information_schema.tables where table_schema = 'gen' order by 1").fetchall()]
    print("| model | rows gen / ref | common columns | only in gen | only in ref | rows gen-only / ref-only | columns whose values differ |")
    print("|---|---|---|---|---|---|---|")
    for table in generated:
        gen_cols = columns(con, "gen", table)
        ref_cols = columns(con, "main", table)
        gen_rows = con.execute(f"select count(*) from gen.{table}").fetchone()[0]
        if not ref_cols:
            print(f"| {table} | {gen_rows} / n/a | no reference model | | | | |")
            continue
        ref_rows = con.execute(f"select count(*) from main.{table}").fetchone()[0]
        common = [c for c in gen_cols if c in ref_cols]

        def select(schema: str, cols: dict[str, str], names: list[str]) -> str:
            return "select " + ", ".join(normalized(c, cols[c]) for c in names) + f" from {schema}.{table}"

        gen_only = except_count(con, select("gen", gen_cols, common), select("main", ref_cols, common))
        ref_only = except_count(con, select("main", ref_cols, common), select("gen", gen_cols, common))
        differing = [c for c in common
                     if except_count(con, select("gen", gen_cols, [c]), select("main", ref_cols, [c]))
                     or except_count(con, select("main", ref_cols, [c]), select("gen", gen_cols, [c]))]
        print(f"| {table} | {gen_rows} / {ref_rows} | {len(common)} | {', '.join(c for c in gen_cols if c not in ref_cols) or '-'} "
              f"| {', '.join(c for c in ref_cols if c not in gen_cols) or '-'} | {gen_only} / {ref_only} "
              f"| {', '.join(differing) or 'none'} |")


if __name__ == "__main__":
    main(sys.argv[1])
