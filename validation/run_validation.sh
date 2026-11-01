#!/usr/bin/env bash
# Build dbt-labs/jaffle-shop and the generated project into one DuckDB file, then compare them.
# Usage: validation/run_validation.sh   (needs network for git clone and dbt deps)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/validation/work"
BIN="${VENV_BIN:-$ROOT/.venv/bin}"
REF_COMMIT=5beb145b

rm -rf "$WORK"
mkdir -p "$WORK"

cat > "$WORK/profiles.yml" <<YAML
jaffle_shop:
  target: dev
  outputs:
    dev: {type: duckdb, path: "$WORK/jaffle.duckdb", schema: main, threads: 4}
flowline_demo_jaffleshop:
  target: dev
  outputs:
    dev: {type: duckdb, path: "$WORK/jaffle.duckdb", schema: gen, threads: 4}
YAML
export DBT_PROFILES_DIR="$WORK"

echo "== reference: dbt-labs/jaffle-shop @ $REF_COMMIT"
git clone --quiet https://github.com/dbt-labs/jaffle-shop.git "$WORK/jaffle-shop"
git -C "$WORK/jaffle-shop" checkout --quiet "$REF_COMMIT"
cd "$WORK/jaffle-shop"
# The reference pins dbt >= 2.0; these edits let it run on dbt-core 1.x with dbt-duckdb.
sed -i '/require-dbt-version/d; /^dbt-cloud:/,/project-id/d; s/^profile: .*/profile: jaffle_shop/' dbt_project.yml
printf 'packages:\n  - git: "https://github.com/dbt-labs/dbt-utils.git"\n    revision: "1.3.0"\n' > packages.yml
rm -f package-lock.yml
"$BIN/dbt" deps --quiet
"$BIN/dbt" seed --vars '{load_source_data: true}'
"$BIN/dbt" build

echo "== generated project"
set +e
"$BIN/python" -m pwc2dbt --input "$ROOT/tests/fixtures/FLOWLINE_DEMO_JAFFLESHOP.xml" --output "$WORK/generated" --all-mappings > "$WORK/convert.log"
status=$?
set -e
echo "converter exit code: $status (2 = written, some models incomplete)"
[ "$status" -le 2 ] || exit "$status"
cd "$WORK/generated"
for command in debug parse compile build; do
    echo "== dbt $command"
    "$BIN/dbt" "$command"
done

echo "== comparison"
"$BIN/python" "$ROOT/validation/compare.py" "$WORK/jaffle.duckdb"
