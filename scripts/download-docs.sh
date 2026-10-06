#!/usr/bin/env bash
#
# Download documentation trees from GitHub repositories into data/.
#
# Only the requested subdirectory is fetched (via a blobless sparse
# checkout), and its relative paths are preserved under data/.
#
# Usage:
#   scripts/download-docs.sh          # download every source below
#   scripts/download-docs.sh jj       # download only the "jj" source
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Sources to download, one per line: "<name> <owner/repo> <ref> <subpath>".
# Files land in data/<name>/<subpath>.
SOURCES=(
  "jj jj-vcs/jj main docs"
)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

download() {
  local name="$1" repo="$2" ref="$3" subpath="$4"
  local dest="$REPO_ROOT/data/$name/$subpath"
  local checkout="$TMP/$name"

  echo "==> $repo@$ref:$subpath -> ${dest#"$REPO_ROOT"/}"

  git clone --quiet --depth 1 --branch "$ref" --filter=blob:none --sparse \
    "https://github.com/$repo.git" "$checkout"
  git -C "$checkout" sparse-checkout set "$subpath"

  rm -rf "$dest"
  mkdir -p "$dest"
  cp -R "$checkout/$subpath/." "$dest/"
}

only="${1:-}"
found=false
for source in "${SOURCES[@]}"; do
  read -r name repo ref subpath <<<"$source"
  if [[ -n "$only" && "$only" != "$name" ]]; then
    continue
  fi
  download "$name" "$repo" "$ref" "$subpath"
  found=true
done

if [[ -n "$only" && "$found" == false ]]; then
  echo "error: no source named '$only' in SOURCES" >&2
  exit 1
fi
