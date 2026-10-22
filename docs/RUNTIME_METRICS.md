# Runtime metrics

Use the optional `RuntimeMetrics` collector to measure operations performed by
Python, without asking an AI agent to estimate counts, duration or GitHub traffic.

```python
from workflow.runtime_metrics import RuntimeMetrics, MeasuredStorage
from workflow.github_api import GitHubRestClient
from workflow.github_storage import GitHubStorage

metrics = RuntimeMetrics()
client = GitHubRestClient(token, metrics=metrics)
backend = GitHubStorage(client, repository="owner/repo", branch="main")
storage = MeasuredStorage(backend, metrics)
# Pass storage to the existing workflow repository; run normal operations.
report = metrics.snapshot()
```

Metrics contain aggregate operation names, attempts, failures, wall-clock
elapsed nanoseconds, and payload sizes in bytes. `github.http.GET` and
`github.http.PUT` count **actual transport requests**, including errors and
the extra reads required for GitHub CAS conflict recovery. Storage counters
are logical operations; GitHub request counters are physical HTTP calls.
No paths, document content, API tokens or HTTP response bodies are retained.

The collector is in-memory and opt-in: it adds **zero extra GitHub API calls
or commits**, never writes to the book and does not alter storage semantics.
Export snapshots only to an approved destination after the operation; do not
write another commit for every measurement. Local and remote tasks may use
the same collector.

Measurements cover calls passing through the measured objects only. They do
not count model tokens, model latency, human intervention, direct filesystem
reads outside the backend, or external GitHub connector calls. They therefore
must not be called an end-to-end translation benchmark on their own.
`session_benchmark.py` still requires independently reviewed comparable
runs and separately observed whole-run elapsed time.
