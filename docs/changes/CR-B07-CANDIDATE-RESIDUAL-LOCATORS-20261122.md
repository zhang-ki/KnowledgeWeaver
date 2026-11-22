# B07 residual RFC TXT locator decision

CR CR-B07-CANDIDATE-RESIDUAL-LOCATORS-20261122 implements the narrow RFC TXT portion authorized on 2026-11-22. Base integration is d633fe3722ce4e31ad8ffaca4777f316ad5cf923.

## Source coordinates

Decode TXT using the existing parser and keep the decoded text unchanged. Compute SourceVersion raw SHA-256 from original bytes. Source spans remain zero-based, half-open decoded character offsets. Display line numbers are one-based and inclusive. CRLF is one line break. CR, LF and FF are separate line breaks. FF also starts the next page at page-relative line 1. Boundary characters belong to the line they terminate. The existing MarkdownLocator line fields store global lines. Page and page-relative line are derived from the same text when needed and do not require new public fields.

For example, in the source represented as `A\f\nB`, `B` begins at offset 3, global line 3, page 2, page-relative line 2. Its existing locator is `{"kind":"markdown","path":"sources/specifications/rfc/example.txt","line_start":3,"line_end":3}`.

The Markdown-kind parser fingerprint includes this TXT coordinate revision because the existing manifest records one parser fingerprint per SourceKind. TXT gets its own chunk fingerprint derived from the caller fingerprint plus the window algorithm revision. Published IDs are not modified in place.

## Boundary coverage

An oversized TXT block produces deterministic contiguous leaf windows up to max_chars with at least half-window overlap. A cross-boundary quote can map through one overlapping leaf only when the full exact quote belongs to one source span, the actual quote's full line interval is inside the declared Gold locator, and Generation Evidence covers that quote. Multiple windows for one occurrence retain one source interval. Occurrences on different source intervals remain distinct and ambiguous if the declared locator cannot disambiguate them. No evidence is fabricated and the existing leaf, hash, path and generation membership rules apply.

HTML and cross-symbol Python are outside this implementation. Use a versioned, independently reviewed data release for corrected quotes.
