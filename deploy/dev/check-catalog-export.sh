#!/usr/bin/env bash
# Check a model-catalog export with the catalog's own validators, before it
# becomes a pull request: unpack the export's zip (Export to model catalog >
# Download) into a scratch worktree of a model-catalog checkout and run
#   npm run validate:schema      the schemas and the catalog's rules, whole catalog
#   npm run validate:helm        each exported variant rendered through its chart
#
#   deploy/dev/check-catalog-export.sh <export.zip> <model-catalog checkout>
#
# The checkout needs `npm ci` and `npm run tools:install` done once (helm and
# kubeconform at the catalog's pinned versions). Nothing in it is changed: the
# worktree is made at origin/master and removed afterwards.
set -euo pipefail
zip=${1:?usage: check-catalog-export.sh <export.zip> <model-catalog checkout>}
catalog=$(cd "${2:?usage: check-catalog-export.sh <export.zip> <model-catalog checkout>}" && pwd)
zip=$(cd "$(dirname "$zip")" && pwd)/$(basename "$zip")

work=$(mktemp -d)
cleanup() { git -C "$catalog" worktree remove --force "$work/catalog" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
git -C "$catalog" fetch -q origin
git -C "$catalog" worktree add -q --detach "$work/catalog" origin/master
cd "$work/catalog"
ln -s "$catalog/node_modules" node_modules
mkdir -p .cache && ln -s "$catalog/.cache/bin" .cache/bin
[ -d "$catalog/.cache/helm-validation" ] && cp -R "$catalog/.cache/helm-validation" .cache/

unzip -q -o "$zip"
variants=$(unzip -Z1 "$zip" | grep -E '^models/[^/]+/[^/]+\.yaml$' | grep -v '/metadata\.yaml$' || true)
echo "== $(unzip -Z1 "$zip" | wc -l | tr -d ' ') files from $(basename "$zip")"
unzip -Z1 "$zip" | sed 's/^/   /'

echo "== validate:schema"
node hack/validate-schema.js
if [ -n "$variants" ]; then
  echo "== validate:helm"
  # shellcheck disable=SC2086
  node hack/validate-helm.js $variants
fi
echo "== the export passes the catalog's checks"
