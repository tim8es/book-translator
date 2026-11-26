# Fallback route readiness

The explicit synthetic qualification profile "routes" checks the three
existing production fallback roles: Translator, Reviewer and Corrector.
The four primary role aliases (including Orchestrator) are counted without
publishing their values. No book is opened, created or modified.

Configure the actual routes in OmniRoute first, then set the existing protected
Actions variables OMNIROUTE_TRANSLATOR_FALLBACK_MODEL,
OMNIROUTE_REVIEWER_FALLBACK_MODEL and OMNIROUTE_CORRECTOR_FALLBACK_MODEL.
Do not copy a primary alias into a backup variable. The runner cannot infer a
provider's real model mapping from aliases alone. The Orchestrator remains
optional bounded advice with a deterministic fallback, not a fourth paid
fallback route.

Run the exact immutable executor with:
```
python automation/quality_qualification.py --revision <40-hex-SHA> --profile routes --output /tmp/route-report.json
```

The probe makes at most three model calls, only for configured aliases distinct
from every primary alias. It verifies strict JSON transport compatibility, not
literary competence or an independent upstream provider. A missing, shared,
unavailable or incompatible fallback fails qualification. The output contains
only sanitized statuses and counts; it never prints model IDs, credentials,
provider responses or provider URLs.

After readiness passes, verify upstream independence in OmniRoute and run
literary qualification with each actual backup before enabling it for ordinary
work. Do not fabricate route names or rewrite the production configuration
from a failed probe.
