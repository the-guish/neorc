#!/bin/sh
# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0
#
# neorc-ui-pack: neorc publishes no node package yet, so a host such as
# Robinauts installs @neorc/ui from a checkout of the neorc commit it pins.
# Run in that checkout, this script packs two tarballs into the directory it
# is given: neorc-ui-0.0.0.tgz, which is @neorc/ui, and oneberth-ui-0.0.0.tgz,
# which is @oneberth/ui from the oneberth commit that this checkout pins. A
# host installs both. Needs Node.js and npm.
set -eu

if [ "$#" -ne 1 ]; then
    printf 'usage: %s <output-directory>\n' "$0" >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$1"
out=$(CDPATH= cd -- "$1" && pwd)

"$root/scripts/oneberth-ui-from-source.sh" >&2
cd "$root/ts/neorc-ui"
# The package ships src/api/schema.d.ts, which `npm run generate` writes.
npm ci --ignore-scripts --silent >&2
npm run --silent generate >&2
npm pack --ignore-scripts --silent --pack-destination "$out" >/dev/null
cp vendor/oneberth-ui-0.0.0.tgz "$out/oneberth-ui-0.0.0.tgz"
for tarball in "$out/neorc-ui-0.0.0.tgz" "$out/oneberth-ui-0.0.0.tgz"; do
    printf '%s\n' "$tarball"
done
