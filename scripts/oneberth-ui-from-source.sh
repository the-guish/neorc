#!/bin/sh
# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0
#
# oneberth-ui-from-source: oneberth publishes no node package yet, so neorc's
# interface installs @oneberth/ui from the oneberth commit that the root
# pyproject.toml pins for the Python package. One pin serves both.
#
# It checks that commit out and packs ts/oneberth-ui into
# ts/neorc-ui/vendor/oneberth-ui-0.0.0.tgz, which ts/neorc-ui/package.json
# names. package-lock.json holds the tarball's integrity, so `npm ci` refuses
# a tarball packed from another commit. CI and a developer run this script
# before `npm ci`. ONEBERTH_REPOSITORY names another clone to fetch from,
# such as a local checkout.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
pins="$root/pyproject.toml"
repository=$(sed -n 's/^oneberth = .*git = "\([^"]*\)".*/\1/p' "$pins")
revision=$(sed -n 's/^oneberth = .*rev = "\([0-9a-f]*\)".*/\1/p' "$pins")
if [ -z "$repository" ] || [ -z "$revision" ]; then
    printf 'pyproject.toml pins no commit of oneberth\n' >&2
    exit 1
fi
repository=${ONEBERTH_REPOSITORY:-$repository}
vendor="$root/ts/neorc-ui/vendor"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'rm -rf "$work"; exit 130' INT
trap 'rm -rf "$work"; exit 143' TERM

git init --quiet "$work/oneberth"
git -C "$work/oneberth" fetch --quiet --depth 1 "$repository" "$revision"
git -C "$work/oneberth" checkout --quiet --detach FETCH_HEAD
fetched=$(git -C "$work/oneberth" rev-parse HEAD)
if [ "$fetched" != "$revision" ]; then
    printf 'fetched %s from %s, and pyproject.toml pins %s\n' "$fetched" "$repository" "$revision" >&2
    exit 1
fi

mkdir -p "$work/packed" "$vendor"
npm pack "$work/oneberth/ts/oneberth-ui" --ignore-scripts --silent \
    --pack-destination "$work/packed" >/dev/null
mv "$work/packed/oneberth-ui-0.0.0.tgz" "$vendor/oneberth-ui-0.0.0.tgz"
printf '@oneberth/ui from oneberth %s: %s\n' "$revision" "$vendor/oneberth-ui-0.0.0.tgz"
