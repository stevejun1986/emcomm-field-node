# scripts/Packages/

Locally cached installers and assets, so a node can be built without re-fetching
large downloads. Contents are `.gitignore`d — `.deb` packages are large and carry
their own licensing.

| Item | Used by |
| --- | --- |
| `satdump_<version>_<distro>_amd64.deb` | SatDump step, if present |

If no SatDump `.deb` is present the provisioner falls back to building from
source, which works but takes considerably longer.

**Verify what you cache.** The provisioner checks a SHA-256 for the SatDump
package before installing it. If you drop in a different version, update the
expected hash in the provisioner to match, or the step will refuse to install it
— which is the correct behavior, not a bug to work around.
