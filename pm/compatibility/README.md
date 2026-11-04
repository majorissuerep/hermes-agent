# Maintained Pillow compatibility packages

These two source distributions retain the upstream runtime code and complete licenses.
Their metadata is maintained by this fork and their installed versions carry
`0.1.13+hermes.2` / `0.8.1+hermes.2`. They accept Pillow 13.0.0, whose release is
older than the fourteen-day quarantine; all original upstream source inputs and
the pinned build tools also predate that cutoff. The normal release-age lock policy
and installed dependency validation remain enabled.

Browser Harness 0.1.13 pins vulnerable Pillow 12.3.0; its upstream fix is still
unreleased: https://github.com/browser-use/browser-harness/pull/931 .
FastEmbed 0.8.1 caps Pillow below 13; its compatible 0.9.1 release has not yet aged.
This is an explicit maintenance commitment until compatible aged upstream releases
can replace these packages. It does not change upstream runtime code or represent
these local versions as upstream releases.

The provenance file records exact source URLs, upload timestamps, hashes, every
count-checked metadata edit, and resulting archive hashes. Archives use stored DEFLATE blocks to keep bytes
identical across zlib and zlib-ng hosts. FastEmbed's otherwise
unbounded build backend is pinned to aged poetry-core 2.5.0. Browser Harness retains
its upstream setuptools 84.0.0 pin. Full upstream licenses remain inside each archive.

To reproduce, download the two exact upstream source archives listed in
`provenance.json` into a disposable directory, then run:

```sh
python scripts/build_pillow_backports.py --input-dir /path/to/inputs --check
```

The archives live under the existing PM package so generated immutable-input
workspaces retain them. PM resolves, builds, and validates their real metadata
through the same lock and installation flow as other dependencies. Native image,
browser, and embedding consumers still require compatibility qualification.
