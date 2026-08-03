---
okf_version: "0.2"
title: lockstep documentation
description: Documents grouped by authority and lifecycle, not by subject.
---

# lockstep documentation

Grouped by **authority and lifecycle** — the question a reader actually
has is "can I rely on this?", and the answer differs per bundle.

| bundle | what it is |
|---|---|
| [audits](audits/index.md) | Point-in-time findings. No finding, verdict, or conclusion here has been altered. Two mechanical edits DO touch these files and neither changes what was found: an OKF header describing the document, and updates to paths that moved when the documentation was reorganised. |
| [guides](guides/index.md) | How to use the system. Some of these are promises made to a reader — the domain-expert guide and both theory-of-operations documents describe behaviour people rely on. Correct them freely; changing what they promise is a different act and belongs in a commit that says so. |
| [notes](notes/index.md) | Working material. No stability promise, and nothing here binds. |
| [proposals](proposals/index.md) | Design documents and accepted work orders. A proposal carries no authority on its own; an accepted plan's authority comes from the commit that adopted it, never from sitting here. Superseded revisions are marked `status: deprecated`. |
| [spec](spec/index.md) | The contract, and the material that qualifies it: the spec and its adopted amendments bind, the addenda are explicitly informative, and the deviations register records where implementation departs. Read each document's `type` before relying on it. |

Vendored third-party references live in `okf/` and are not
reorganised or annotated by this repo.
