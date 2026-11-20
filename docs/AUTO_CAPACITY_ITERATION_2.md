# Capacity iteration 2

Previous 3600-second real test: 2 substantive chapters, 1 translated, 0 reviewed,
12 reserved calls, 2 provider errors, 20 minutes of mandatory backoff. No claim
of target attainment. The new opt-in paired review path avoids large initial
reviews on aligned chapters, presents exact source/target pairs, compiles
explicit model verdict/checks/evidence to canonical v2, and preserves the
existing independent promotion gate. It also recognizes a first slow size
failure and partitions conservatively without bypassing provider backoff.

After a failed local correction, one complete-block reconstruction can generate
the exact existing anchored edit. The old prefix and suffix must match
byte-for-byte; the normal transactional guard and independent reviewer still
apply. This is not a full-block rewrite outside the pinned reviewer coordinates.

No model IDs or credentials are changed. Fallback model variables were empty in
the previous live test; a distinct supported backup route is an external
dependency, and a provider outage can make the wall-clock SLO unattainable.
Rerun the same two public-domain chapters with compact mode enabled and report
aggregate error codes, accepted operations and chunk progress. Capacity must
not be inferred from regression tests or a small smoke.
