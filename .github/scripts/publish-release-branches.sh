#!/usr/bin/env bash
# Usage: publish-release-branches.sh <version> <dist dir>
# For each <dist>/lore-dcc-<dcc>-<version>.zip, commits the zip's contents plus a VERSION file
# onto release/<dcc> in origin. A rerun for the same version adds nothing.
set -euo pipefail
version=$1
dist=$2

git fetch --quiet origin '+refs/heads/release/*:refs/remotes/origin/release/*'

shopt -s nullglob
zips=("$dist"/lore-dcc-*-"$version".zip)
test ${#zips[@]} -gt 0 || { echo "no lore-dcc-*-$version.zip in $dist"; exit 1; }

for zip in "${zips[@]}"; do
  dcc=${zip##*/lore-dcc-}
  dcc=${dcc%-"$version".zip}
  branch=release/$dcc
  stage=$(mktemp -d)
  unzip -q "$zip" -d "$stage"
  printf '%s\n' "$version" > "$stage/VERSION"

  index=$(mktemp -u)
  GIT_INDEX_FILE=$index git -c core.autocrlf=false --work-tree="$stage" add --all --force .
  tree=$(GIT_INDEX_FILE=$index git write-tree)
  rm -rf "$stage" "$index"

  parent=$(git rev-parse --quiet --verify "refs/remotes/origin/$branch" || true)
  if [ -n "$parent" ] && [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
    echo "$branch already holds v$version"
    continue
  fi
  commit=$(git commit-tree "$tree" ${parent:+-p "$parent"} -m "lore-dcc v$version ($dcc)")
  git push origin "$commit:refs/heads/$branch"
  echo "$branch -> $commit (v$version)"
done
