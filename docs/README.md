# docs/

PDFs placed here are copied to `~/EMCOMM_Data/PDF_Manuals/` and served read-only
over loopback at `http://127.0.0.1:8085` — so the reference library stays usable
with no network, from a browser on the node itself.

**Two things ship here; the rest is yours.** `OPERATORS_MANUAL.md` is this node's
own manual, and this README. Everything else is `.gitignore`d — reference material
is large and licensing varies per document, so add what your group is entitled to
distribute.

## The operator's manual

`OPERATORS_MANUAL.md` is the source, and no PDF of it ships — not in the tarball,
not on a release. If you want the manual on the node, build it from the
provisioner's directory **before you provision**:

    sudo apt install python3-reportlab       # once
    python3 scripts/build_manual.py

The PDF lands here as `EmComm-Operators-Manual.pdf`, and the next provisioning run
carries it to the node's own document server — which is the point, since the manual
is most wanted on a node that has no network to fetch it over. PDFs here are
`.gitignore`d, so a built copy never ends up in the repository.

Commonly useful for an EMCOMM node:

* Your group's own activation plan, net scripts, and ICS forms
* Local frequency lists, repeater directories, and served-agency contact sheets
* Equipment manuals for the radios and interfaces actually deployed
* Public-domain or open-licensed field references

Check the license before adding anything. "Freely downloadable" is not the same
as "redistributable" — several widely circulated manuals are neither.

Keep filenames descriptive; the document server presents a plain directory index,
so the filename is the only thing an operator has to go on at 3am.
