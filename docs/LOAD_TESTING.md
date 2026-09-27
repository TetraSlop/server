# Repeatable load tests

From an editable installation at the repository root:

```powershell
python -B -m tools.load_test --backend memory --players 32 --seconds 10 --output results/memory-32.json
python -B -m tools.load_test --backend json --players 32 --seconds 10 --output results/json-32.json
$env:TM_LOAD_DATABASE_URL = 'postgresql+psycopg://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/postgres'
python -B -m tools.load_test --backend postgres --players 32 --seconds 10 --output results/postgres-32.json
```

Use the same interpreter that has the editable package installed (for example,
`.venv/Scripts/python` on Windows). PostgreSQL requires the optional extra and a
disposable test host whose role can create/drop databases. The harness creates
and removes its own random `tm_load_...` database. It deliberately uses
`TM_LOAD_DATABASE_URL`, never the normal server URL. JSON uses a temporary save.
There is no option to benchmark against an existing save or running game server.

`--players` sets 1..128 connected synthetic clients. `--population` adds dormant
accounts, up to 10,000 total; it must be at least two and at least `--players`.
For example, add `--population 1000` to measure larger storage without claiming
1,000 connected players. `--think-ms 250` inserts a delay between each client's
operations; the default is a closed loop with no delay. `--login-concurrency`
controls warmup separately (default four). Every run has a bounded duration.

`python -B -m tools.load_matrix` repeats the baseline serially: 32 and 128
clients for each backend, plus eight-client cases with eight and 1,000 total
accounts for durable backends. PostgreSQL is included only when `TM_LOAD_DATABASE_URL` is
set. It writes individual reports and logs to `results/load-baseline/`.

## What it measures

The server runs in a separate child process. Clients use the recovered legacy
key exchange, checksums, encryption and authenticated service requests. Every
eight operations contain six heartbeat requests, one saved option change and
one owned-card deck edit. The edit alternates values so writes are real changes.
Each edit includes a heartbeat barrier to establish completion; an operation
is not the same as a packet. Responses are checked before counting success.

JSON output records operations/second, p50/p95/p99/max latency by operation,
login latency, server CPU time, synchronous storage-call latency and event-loop
delay sampled every 10 ms. CPU time excludes the load-client process. File
traffic logging is disabled. Login, provisioning and reload validation happen
outside the measured request interval. The run ends by reloading durable state
and checking every client's last acknowledged option and deck change.

Exit status is nonzero on protocol, persistence or cleanup failure. Generated
JSON and logs belong in ignored `results/`; keep a dated summary when comparing
changes. Run benchmarks serially with other tests and heavy work stopped.

## Interpretation

This is a local closed-loop persistence and responsiveness probe. It does not
simulate complete games, large auction/rating journals, WAN latency, public
authentication, traffic logs or thousands of simultaneous players. Client-side
Python encryption can also limit generated load. Short runs show bottlenecks;
they are not a production capacity estimate or a hosting-cost forecast.

Synchronous saves block the event loop today. Moving writes to an async driver
alone would allow the current staged transaction to yield midway through its
ownership window. A future concurrency change needs explicit serialized commit
ownership and ordering, with the existing rollback/recovery guarantees retained.

## Baseline: 2026-09-27

Windows 11, Python 3.13.15; PostgreSQL 16.15 in a private WSL container on a Linux
volume, with synchronous commit and fsync enabled. Single serial pass, ten-second
request window per case, no think time. In-flight requests drain after the
window; actual elapsed time reached 19.5 seconds in the slowest case. All ten
runs had zero failures. Every durable run reloaded and verified all tested
accounts. [Raw measurements and probes](measurements/2026-09-27.json) include the
actual operation counts, quantiles, CPU time, save latency and sample counts.

| Backend | Clients | Saved accounts | Operations/s | Heartbeat p95, ms | Option-save p95, ms | Max loop stall, ms |
|---|---:|---:|---:|---:|---:|---:|
| memory | 32 | 32 | 4415.4 | 11.4 | 18.1 | 20.3 |
| memory | 128 | 128 | 4388.9 | 46.8 | 72.4 | 71.3 |
| json | 8 | 8 | 89.6 | 211.2 | 489.4 | 1119.0 |
| json | 32 | 32 | 300.3 | 284.3 | 494.6 | 438.2 |
| json | 128 | 128 | 72.9 | 2798.4 | 6076.2 | 5578.3 |
| json | 8 | 1000 | 18.9 | 1104.7 | 1601.6 | 1593.7 |
| postgres | 8 | 8 | 50.1 | 517.3 | 532.3 | 915.9 |
| postgres | 32 | 32 | 44.4 | 2228.9 | 1925.8 | 3175.4 |
| postgres | 128 | 128 | 33.9 | 9538.6 | 7178.8 | 12288.7 |
| postgres | 8 | 1000 | 45.3 | 519.7 | 570.7 | 902.2 |

These numbers identify two current bottlenecks. The memory-only server used
roughly one CPU core and stayed near 4,400 operations/s at both client counts.
The JSON backend repeatedly reads/rewrites the full save: 1,000 accounts produce
about 26 MB, and storage latency grows accordingly. The synchronous call blocks
unrelated clients, so a sequence of individually shorter writes creates long
loop stalls.

The PostgreSQL result needs an environment qualification. A separate read-only
probe measured SELECT 1 at 0.38 ms median, a 16-byte bound parameter at 0.51 ms,
and an 8 KB bound parameter at 44.03 ms. Tracing isolated saves showed about
43 ms in each account UPDATE or card INSERT, while the revision UPDATE and
card DELETE took around 1 ms. This points to payload/transport overhead in this
Windows-to-WSL path; it does not establish the exact cause. Durability was not
disabled to improve the numbers. Repeat on the intended Linux host before
using these results for a hosting decision.

With eight clients, PostgreSQL measured 50.1 operations/s at eight accounts
and 45.3 at 1,000 accounts, consistent with its selected-account write path.
It still blocked the event loop throughout each call. The JSON runs also show
substantial local I/O variability: the eight-account run was slower than the
32-account run. These single passes are not a monotonic capacity curve.
The next scaling work is to establish a same-host Linux baseline,
then isolate durable writes behind explicit serialized commit ownership.
Heavy service-journal histories and full matches need their own workloads.
The highly stalled cases have very few loop-delay samples, so their maxima
show observed pauses rather than a stable percentile estimate.
