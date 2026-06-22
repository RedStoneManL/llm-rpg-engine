#!/usr/bin/env bash
# Regenerate code-graph artifacts across ALL packages.
# ctags (symbols) + pyan3 (call graph) work offline with `python3`.
# The AUTHORITATIVE module dependency graph is the AST scan in docs/codegraph/INDEX.md
# (pydeps under this multi-package layout is finicky — left as an optional manual step).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
OUT="docs/codegraph"
PKGS="engine kernel facts systems llm memory context loop app"

# 1) symbol index (jump-to-def)
if command -v ctags >/dev/null; then
    ctags -R --languages=Python -f "$OUT/tags" $PKGS bin 2>/dev/null \
        && echo "tags:  $(grep -vc '^!' "$OUT/tags") symbols"
fi

# 2) function-level call graph (pyan3, heuristic). .dot is grep-able; SVG optional.
# shellcheck disable=SC2046
if command -v pyan3 >/dev/null; then
    pyan3 $(for p in $PKGS; do echo $p/*.py; done) --uses --no-defines --grouped --dot \
        > "$OUT/app_calls.dot" 2>/dev/null \
        && echo "calls: $(grep -cE '\->' "$OUT/app_calls.dot") edges" || echo "calls: (pyan3 skipped)"
    # visual: dot -Tsvg "$OUT/app_calls.dot" -o "$OUT/app_calls.svg"
fi

# 3) module dependency graph: see docs/codegraph/INDEX.md "Layered architecture" (AST scan).
echo "deps:  see docs/codegraph/INDEX.md (AST dependency graph — authoritative)"
