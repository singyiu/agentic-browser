# agentic-browser / fork

Overlay-style Chromium fork. The upstream checkout in `../mac/src` is kept
pristine; our customizations live here as patches and file overrides that are
applied on top of `src/` before each build.

## Layout

- `PINNED_CHROMIUM` — upstream commit SHA the patches/overrides target.
- `patches/` — `*.patch` files, applied in sorted order with `git apply --3way`.
- `overrides/` — files mirroring `src/` paths; copied over `src/` 1:1.
- `scripts/` — sync / apply / reset / build helpers.

## Workflow

```sh
bash scripts/reset-src.sh        # undo any prior overlay in src/
bash scripts/sync.sh             # gclient sync to PINNED_CHROMIUM
bash scripts/apply-patches.sh    # lay our changes over src/
bash scripts/build.sh            # autoninja -C out/Release chrome
```

## Capturing a new patch

After hacking on `../mac/src`:

```sh
git -C ../mac/src diff > patches/NNN-short-name.patch
```

For brand-new files or whole-file replacements, drop them into
`overrides/<same-path-as-in-src>`.

## Bumping Chromium

1. Pick a new upstream SHA.
2. `echo <sha> > PINNED_CHROMIUM`
3. `bash scripts/reset-src.sh && bash scripts/sync.sh`
4. `bash scripts/apply-patches.sh` — resolve any 3-way conflicts.
5. Re-export resolved patches as in "Capturing a new patch".
6. `bash scripts/build.sh`.

## Constraints

- Never commit changes inside `../mac/src` — that repo always reflects upstream.
- Do not push `../mac/src` to any remote.
- `fork/` is an independent git repo from `mac/src`.
