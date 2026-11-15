# neorc-ui

The built web UI of the neorc manager, as a Python distribution: static
assets and one function, `neorc_ui.static_dir()`, that says where they are.
The UI is standalone neorc: oneberth's shell with one application, neorc.
`neorc manager start` serves them at `/ui/`; `pip install 'neorc[manager]'`
brings this package with it.

It has no dependencies and nothing to run. The assets are built from
`ts/neorc-ui` in the repository: by CI for a release, or by the wheel build
when they are missing, which needs Node.js and git. The wheel build first
packs `@oneberth/ui` from the pinned oneberth commit. A wheel built from the
sdist needs none of them, since the sdist carries the assets.

In a checkout, `uv sync` installs this package editable with no assets;
`static_dir()` then raises an error naming the fix. Python work never needs
them: the manager's tests mount a stand-in directory.

See the repository's [README](https://github.com/open-shipyard/neorc) and
[CHANGELOG](https://github.com/open-shipyard/neorc/blob/main/CHANGELOG.md).
