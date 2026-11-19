# Source-owned review refactor (phase 1)

Use source-owned structural span IDs and exact rendered coordinates, not inferred cross-language boundaries. Immutable maps bind both artifact hashes and each paired interval. Corrections must reproduce the actual candidate; ambiguous cross-span edits invalidate map reuse. ID-addressed independent findings compile to the pinned v2 exact anchor schema and retain prior lineage and canonical validators. No source changes or silent pin upgrades. This commit supplies primitives and tests; runtime wiring and capacity proof remain separate.
