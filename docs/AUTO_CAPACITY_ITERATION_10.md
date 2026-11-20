# Capacity iteration 10: complete mapped review before partitioning

Run 44615000992 on b45d17932473fb3788a6c05cada2a3c4a87b8cfc: same source hash, 2 translated / 0 reviewed, 22 reserved calls, 3600.08 seconds, 2 provider errors, 383399 reported tokens across claimed roles, no optional memory proposal errors. Previous run: 27 calls and 0 reviewed. Reduced calls are not an accepted-chapter throughput improvement.

Every rebase excluded all three windows due to a nonlocal prior finding; focused review also refused the scope. First chapter unresolved counts were 7, 7, 9 (new findings 7, 6, 8; resolved prior rechecks 0, 6, 6). Do not relax nonlocal safety gates.

Opt-in experimental route: when no durable chunk stage or provider pressure/retry exists and the exact complete mapped packet fits the existing safe prompt cap, do one full independent ID-addressed review (all source and target bytes, all checks, all prior unresolved rechecks, chapter-wide continuity). On failure/restart the reserved ordinary chunk fallback remains durable. A successful canonical record clears fallback flags. Oversized or unsuitable input retains existing route. No book pin or acceptance rule changes.
