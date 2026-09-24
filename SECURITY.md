# Security

## Reporting

Open a [security advisory](../../security/advisories/new) for anything you would
rather not discuss in a public issue. For everything else an ordinary issue is
fine and gets seen sooner.

There is no bounty and no SLA. This is a volunteer project; expect a human
answer, not a process.

## What counts as a security issue here

Three kinds, in the order they would matter to an operator:

**Anything that could key a transmitter unintentionally.** A sound-card radio
interface keys from a serial control line, and opening the port asserts it. A
change that causes the provisioner, a verification check, or a service it
installs to open a declared radio interface can put a station on the air — into
an antenna that may not be connected, under a callsign that may be a
placeholder. That is a safety problem before it is a software problem. Report it
as one.

**Operator data reaching the repository or a release.** Shipped configuration
carries substitution tokens, never a real callsign, grid square, audio device
name or absolute home path. If you find any of those committed, say so; this
repository has had to rewrite its history once already for exactly that.

**Anything affecting the integrity of a release artifact.** See below.

## Verifying a release

Release tarballs are signed. The key, the `allowed_signers` line and the
`ssh-keygen -Y verify` invocation are in the README under **Verifying a release
download**, along with the key fingerprint for checking a copy obtained
elsewhere.

**If `sha256sum -c` or the signature check fails, do not run the provisioner.**
Report it.

## Scope

This repository covers the provisioner, the tile fetcher, the configuration
templates and its own documentation. The applications it installs — JS8Call,
Direwolf, QMapShack, SatDump and the rest — are upstream projects with their own
security contacts. A vulnerability in one of those belongs with its maintainers,
though a note here is welcome if it changes what this provisioner should do.
