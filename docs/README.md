# docs/

PDFs placed here are copied to `~/EMCOMM_Data/PDF_Manuals/` and served read-only
over loopback at `http://127.0.0.1:8085` — so the reference library stays usable
with no network, from a browser on the node itself.

**Two things ship here; the rest is yours.** `OPERATORS_MANUAL.md` is this node's
own manual, and this README. Everything else is `.gitignore`d — reference material
is large and licensing varies per document, so add what your group is entitled to
distribute.

## The operator's manual

`EmComm-Operators-Manual.pdf` is published as an asset on the release. **Download it
and drop it in this directory before you provision**, and the node's own document
server will carry it — which is the point, since the manual is most wanted on a node
that has no network to fetch it over.

It is not in the release tarball. PDFs here are `.gitignore`d, so a built manual
cannot ride along in an archive of the repository; the markdown source does ship,
and `scripts/build_manual.py` rebuilds the PDF from it if you would rather build
than download:

    python3 scripts/build_manual.py          # needs reportlab

Either way the PDF lands here and is picked up by the next provisioning run.

Commonly useful for an EMCOMM node:

* Your group's own activation plan, net scripts, and ICS forms
* Local frequency lists, repeater directories, and served-agency contact sheets
* Equipment manuals for the radios and interfaces actually deployed
* Public-domain or open-licensed field references

Check the license before adding anything. "Freely downloadable" is not the same
as "redistributable" — several widely circulated manuals are neither.

Keep filenames descriptive; the document server presents a plain directory index,
so the filename is the only thing an operator has to go on at 3am.
