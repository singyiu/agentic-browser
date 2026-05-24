# agentic-browser

Overlay-style Chromium fork. The upstream checkout in `mac/src` is kept pristine;
our customizations live here as patches, file overrides, and build config that are
applied on top of `mac/src` before each build.

`mac/` (the full Chromium checkout, ~100 GB, gclient-managed) is **git-ignored** —
it is reproducible from `PINNED_CHROMIUM` via `gclient sync`, so it is never
committed to this repo.

## Layout

- `PINNED_CHROMIUM` — upstream commit SHA the patches/overrides target.
- `patches/` — `*.patch` files, applied in sorted order with `git apply --3way`.
- `overrides/` — files mirroring `mac/src/` paths; copied over `mac/src/` 1:1.
- `config/args.gn` — GN build args (RAM-bounded for an 18 GB machine; see below).
- `scripts/` — sync / apply-patches / setup-gn / build / reset / mem-watch helpers.
- `mac/` — the Chromium checkout (git-ignored, not part of this repo).
- `agent-backend/` — **Aegis**, the Claude-Max agentic browser-control backend with a parental guardian (Python; see `agent-backend/README.md`).

## Workflow

```sh
bash scripts/reset-src.sh        # undo any prior overlay in mac/src
bash scripts/sync.sh             # gclient sync to PINNED_CHROMIUM
bash scripts/apply-patches.sh    # lay our changes over mac/src
bash scripts/setup-gn.sh         # write out/Release/args.gn + gn gen
bash scripts/build.sh            # RAM-bounded build (autoninja -j6); BUILD_JOBS=N to override
```

`build.sh` runs `setup-gn.sh` automatically if `out/Release/args.gn` is missing,
so you can't accidentally build with Chromium's memory-hungry defaults.

## Build config & memory

This repo targets an 18 GB Apple-silicon Mac, which cannot run Chromium's default
build parallelism without exhausting RAM and hanging the host. `config/args.gn`
pins a RAM-safe configuration:

- `is_component_build = true` — many small `.dylib` links instead of one giant
  static link, so no single link spikes memory.
- `concurrent_links = 1` — never overlap heavy link steps.
- `build.sh` caps compile jobs at `-j 6` (override with `BUILD_JOBS=N`).
- `scripts/mem-watch.sh` — optional watchdog; run it in a second terminal to
  monitor memory pressure and kill the build before it can OOM the host.

## Capturing a new patch

After hacking on `mac/src`:

```sh
git -C mac/src diff > patches/NNN-short-name.patch
```

For brand-new files or whole-file replacements, drop them into
`overrides/<same-path-as-in-mac/src>`.

## Bumping Chromium

1. Pick a new upstream SHA.
2. `echo <sha> > PINNED_CHROMIUM`
3. `bash scripts/reset-src.sh && bash scripts/sync.sh`
4. `bash scripts/apply-patches.sh` — resolve any 3-way conflicts.
5. Re-export resolved patches as in "Capturing a new patch".
6. `bash scripts/setup-gn.sh && bash scripts/build.sh`.

## Constraints

- Never commit changes inside `mac/src` — that checkout always reflects upstream.
- Do not push `mac/src` to any remote.
- `mac/` is git-ignored; the Chromium source is reproduced via `PINNED_CHROMIUM`.
