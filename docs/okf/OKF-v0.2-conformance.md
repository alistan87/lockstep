# Open Knowledge Format v0.2 — vendored conformance reference

**Source:** `GoogleCloudPlatform/knowledge-catalog`, `okf/SPEC.md`
**Retrieved:** 2026-08-02
**Pinned at:** v0.2

> **This is a retrieved SUMMARY, not the verbatim specification.** It was
> produced by reading the upstream page, so it may omit or compress normative
> detail. Where this file and upstream disagree, **upstream wins** — treat this
> as a working reference for the validator in `contrib/hygiene/okf.py`, not as
> the authority. Re-fetch and diff before any bump; the spec is young and moving,
> which is exactly why it is pinned rather than tracked.

## Required frontmatter

| Field | Rule |
|---|---|
| `type` | **Required, non-empty.** A short string identifying the kind of concept. Consumers use it for routing, filtering, and presentation. |

## Recommended frontmatter

| Field | Meaning |
|---|---|
| `title` | Human-readable display name |
| `description` | Single-sentence summary |
| `resource` | URI uniquely identifying the underlying asset |
| `tags` | YAML list of categorisation strings |

## Optional field families

**Provenance — `sources`**: a list; each entry requires `resource`, and may
carry `id`, `title`, `author`, `usage_count`, `last_modified` (YYYY-MM-DD).
Sibling field `usage_window: { from, to }`.

**Trust** — `generated: { by, at }` and `verified: [{ by, at }]`, `at` being an
ISO-8601 datetime.

**Lifecycle** — `status: draft | stable | deprecated` (default `stable`) and
`stale_after` (YYYY-MM-DD).

**Attested computation** (type-specific) — `runtime` (required for that type),
`parameters: [{ name, type, required }]`, `computation`, `executor`, `attester`.

## Actor convention

Used by `generated.by` and `verified[].by`:

- agents/tools — `<producer>/<version>`, e.g. `reference_agent/gemini-2.5-pro`
- people — `human:<id>`
- processes — `process:<id>`

## Bundle structure

```
bundle/
  index.md          # optional directory listing   (RESERVED name)
  log.md            # optional chronological history (RESERVED name)
  <concept>.md      # concept documents
  <subdirectory>/   # hierarchical organisation
```

A bundle may declare its target version with `okf_version: "0.2"` in the
frontmatter of the **bundle-root** `index.md`.

## Conformance

A bundle is conformant if:

1. every non-reserved `.md` file contains parseable YAML frontmatter;
2. every frontmatter block contains a non-empty `type`;
3. reserved filenames follow their specified structures when present.

**Consumers must not reject a bundle** for missing optional fields, unknown
`type` values, or broken links. This is a liberal-in-what-you-accept format, and
the validator here honours that: unknown types are permitted, and a missing
recommended field is a note, never a failure.

## Types used by this repository

`type` is an open string, so this list is a local convention rather than a
constraint imposed by the format:

| `type` | Applies to |
|---|---|
| `specification` | the normative spec |
| `amendment` | adopted amendments to it |
| `addendum` | informative, spec-adjacent governance |
| `register` | living records (e.g. deviations) |
| `theory-of-ops` | why the system behaves as it does |
| `guide` | how to do a thing |
| `proposal` | design documents under consideration |
| `plan` | accepted work orders |
| `report` | point-in-time audits |
| `notes` | working notes, no stability promise |
