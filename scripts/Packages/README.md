# scripts/Packages/

Locally cached installers and assets, so a node can be built without re-fetching
large downloads. Contents are `.gitignore`d — `.deb` packages are large and carry
their own licensing.

Nothing is cached here by default; the directory ships empty.

SatDump is **not** cached here. It is built from source on every node: upstream
publishes `.deb` packages on GitHub releases but runs no apt repository, so a
packaged install meant carrying a `.deb` and its SHA-256 in this repository and
revising both on every release.

**Verify what you cache.** Anything placed here is installed without this
repository having vouched for it. Check provenance and integrity yourself.
