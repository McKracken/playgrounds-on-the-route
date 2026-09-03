# POI Playground Classifier (CLI) — Spec Critique v2 (Codex)

## Verdict

The v1 changes address most of the twelve blockers, but three fixes are not yet internally complete (input-error routing, browser ownership, and URL canonicalization). I found five additional blocking contract inconsistencies. This review is based only on the spec text and the prior critique; no network or external-syntax re-verification was performed.

## Prior 12 blocking items — brief audit

1. **Resolved — image limits. [verified]** FR-4.2 and Integration Points consistently say under/at most 10 MB base64-encoded and 8000×8000 pixels.
2. **Not fully resolved — `INVALID_INPUT` vs `POI_NOT_FOUND`. [verified]** The rule text is now disjoint, but FR-1.3's own Verify example still contradicts it; see Finding 1.
3. **Resolved, except for `INTERNAL_ERROR`. [verified]** FR-5.1 now defines nullable error output, but AR-7.2 conflicts with it for internal failures; see Finding 6.
4. **Resolved — evidence timing. [verified]** FR-6.1 stages positives until a final positive decision, consistently with FR-6.2 and its negative test.
5. **Resolved — vision-model configuration. [verified]** FR-4.2/FR-7.1 consistently make `--vision-model` required with no default.
6. **Resolved — photo acquisition. [verified]** FR-3.2 defines URL-first acquisition, screenshot fallback, deduplication, order, cap, and exhaustion.
7. **Rule added, but interface remains inconsistent. [verified]** FR-4.3 defines aggregation, but nullable per-photo confidence makes that rule undefined; see Finding 4.
8. **Resolved — structured output. [verified]** FR-4.2/AR-4.2 require forced tool use with a schema, and FR-4.4 handles malformed results.
9. **Not fully resolved — browser ownership. [verified]** AR-1.1 and FR-3.1 still assign creation differently; see Finding 2.
10. **Resolved — OSM relations. [verified]** FR-2.1 consistently specifies `nwr` and tests relation-only results.
11. **Resolved — timeout separation. [verified]** FR-2.3 separately defines server timeout and client timeout (`+5` seconds).
12. **Not fully resolved — URL canonicalization. [verified]** Precedence is defined, but the selected token/query branches still lack a consistent resolution/browser contract; see Finding 3.

## Blocking self-consistency findings

1. **FR-1.3's Verify line contradicts FR-1.1 and FR-1.3's rule text. [verified]** FR-1.1 makes every non-empty unmatched string a free-text query. FR-1.3 says a zero-result free-text query returns `POI_NOT_FOUND`, yet its Verify line requires an “empty/gibberish-but-non-coordinate-shaped input” to return `INVALID_INPUT`. Empty and non-empty gibberish cannot share that expectation. Change the example to empty input only, or expect `POI_NOT_FOUND` for mocked zero-result gibberish.

2. **Browser/context creation has two owners, and AR-1.1 says both “exactly one” and zero contexts are possible. [verified]** AR-1.1 says `cli.py` creates and passes the browser/context, while FR-3.1 says `gmaps_scraper` launches Chromium unless it can reuse a context. AR-1.1 also says “Exactly one context is created per CLI invocation,” then says an URL/coordinate OSM hit never creates one. Specify `cli.py` as sole owner and say **at most one** context is created.

3. **The canonical URL branches do not consistently define how a `ResolvedPOI` is produced. [verified]** FR-1.2 requires `lat`, `lng`, and non-null `maps_url`, but does not say how an opaque place ID/data token yields coordinates, whether resolution falls through when the higher-precedence token cannot resolve, or how a bare coordinate gets its required `maps_url`. A canonicalized URL query may be submitted as a search (FR-1.3), but AR-1.1 permits a Playwright context “only for the free-text path.” Meanwhile FR-3.1 describes a fallback “if no direct URL exists,” although the data model does not allow `maps_url=None`. These choices would yield incompatible resolver interfaces and error routing. Define the resolution operation/fallback for each URL candidate and either make `maps_url` nullable or require construction of a canonical Maps URL for every successful input.

4. **Nullable classifier confidence makes positive aggregation undefined. [verified]** FR-4.1 and Data Requirements permit `ClassificationResult.confidence=None`; FR-4.3 requires `min()` across every qualifying positive confidence. AR-4.2 guarantees a number only for the Claude implementation, while the interface and its fake-classifier Verify case are deliberately generic. Require a `[0,1]` float on every successful positive classification, or specify exactly how `None` participates in final aggregation.

5. **Standard argparse behavior contradicts the global JSON/exit-code contract. [verified]** FR-7.1 and AR-7.1 require standard argparse errors with exit code 2 (and `--help` with help text). AR-7.2 says exactly one JSON document is written per run and that exit code 1 is the only nonzero code. Explicitly exempt help and argument-parse/validation termination from AR-7.2, or define custom JSON-emitting parser behavior. FR-7.1's Verify scenarios currently cannot satisfy AR-7.2 as written.

6. **`INTERNAL_ERROR` cannot satisfy both AR-7.2 and the Feature 5 schema. [verified]** FR-5.1/Data Requirements require `input` always present and `evidence` to be a list; AR-7.2 says an internal-error document has “other fields `null`,” which would make both invalid. It is also unspecified whether a late internal failure retains an already-populated `resolved`. Define the full `INTERNAL_ERROR` object field-by-field (at minimum raw `input`, `evidence=[]`, and a stage-based rule for `resolved`).

7. **Evidence sidecar writes have no atomicity rule. [verified]** FR-6.1 requires a photo plus matching sidecar. FR-6.3 says failure writing either causes “that photo” to be omitted from `evidence`, but does not say whether a successfully written half-pair is removed. Its Verify line checks only the evidence list, so both orphan-producing and rollback implementations pass. Require per-photo pair atomicity/cleanup and assert that no orphan photo or sidecar remains.

8. **The checklist's required test feature does not exist. [verified]** The Testing Strategy row says “Feature 8 (FR-8.1)” requires pytest coverage and opt-in integrations, but the spec ends at Feature 7 and contains no FR-8.1. Either add that feature/requirement or make the checklist refer to the existing per-FR Verify lines without claiming a nonexistent requirement.

## Smaller ambiguity worth fixing with the blockers

- **Sidecar POI identity is not represented by the data model. [verified]** FR-6.1/Data Requirements require “POI id/coordinates,” but `ResolvedPOI` has no ID and the slash does not establish whether ID, coordinates, or both are mandatory. State the exact sidecar keys and their nullability.
- **FR-5.2's comparison is format-ambiguous. [verified]** It requires the output file to be pretty-printed and its “content” to match stdout, whose formatting is unspecified. Say the parsed JSON values must be equal, or require identical serialization on both streams.

## Required changes before implementation

Correct the FR-1.3 Verify case; make browser ownership and context cardinality singular; fully define every `ResolvedPOI` branch; resolve nullable-confidence aggregation; carve parser/help behavior out of (or reconcile it with) AR-7.2; define a schema-valid `INTERNAL_ERROR`; require atomic evidence pairs; and remove or supply the dangling Feature 8 reference.
