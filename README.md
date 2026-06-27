# x-video-downloader (`xdl`)

Paste an X (Twitter) post link → get the highest-quality video. On macOS it lands in
`~/Downloads`; on iPhone it lands in **Photos** (via an always-on engine + a Shortcut).
Concurrent downloads, automatic retries, a persistent ledger of failures, and dedup.

> **One engine, two thin clients.** Everything hard — extraction, concurrency, retries,
> the failed-link ledger, dedup — lives in one `core`. The clients (CLI, iOS Shortcut)
> only capture a URL and handle local delivery. Migrating from "laptop now" to a "home
> server later" is a config change, not a rewrite.

## What you get

- **Highest-quality, no re-encode.** `yt-dlp` grabs best video+audio and remuxes to a
  Photos-compatible **H.264/AAC MP4** — no transcode, no quality loss.
- **Anonymous-first, cookie-fallback.** Most public X videos download with no account
  at all. Only on a login/sensitive-content wall does it retry with burner-account
  cookies (passing cookies can *break* downloads that work anonymously, so they're a
  fallback by design).
- **Retries that don't lose links.** Three independent layers: yt-dlp's in-attempt
  retries, the anonymous→cookie auth fallback, and a whole-post retry (3×, 60 s apart).
  Exhausted jobs land in a queryable `dead` state — re-drive them all with one call.
- **Dedup.** A yt-dlp download-archive means re-shares and re-drives never re-download
  something you already have.
- **Two iOS delivery tiers.** Synchronous (lands in Photos immediately, for one-offs)
  and async + collector (background, for batches/large files).

## Architecture

```
src/xdl/
├── config.py      # env-driven paths (XDL_DB, XDL_ARCHIVE, XDL_DOWNLOAD_DIR, …)
├── ledger.py      # SQLite source of truth: jobs table, status lifecycle
├── extractor.py   # yt-dlp wrapper, error classification, anonymous→cookie fallback
├── runner.py      # one attempt → ledger transition (queue-independent, unit-tested)
├── tasks.py       # Huey (SQLite) scheduler: bounded concurrency + delayed retries
├── cli.py         # `xdl` — local one-shot client (no server needed)
└── server.py      # FastAPI surface (iOS clients; sync + async)
```

Three flexibility levers make migration config-only: the `core`/`cli`/`server` split,
every path externalised to an env var, and clients addressing a stable Tailscale
MagicDNS name instead of an IP. See [the original plan](#plan) for the full rationale.

| | Now | Future (home stack) |
|---|---|---|
| Always-on engine | Oracle Always Free (ARM), Docker | Mac mini / Pi, same image |
| Desktop | Mac-local → `~/Downloads` | Mac → home engine (or stay local) |
| State (ledger/archive/staging) | env-configured local paths | NAS export, one shared ledger |
| Reach the engine | Tailscale name → Oracle | reassign same name → home box |

## Quickstart (macOS, local)

```bash
git clone https://github.com/zachtheyek/x-video-downloader.git
cd x-video-downloader
python3 -m venv .venv
.venv/bin/pip install .          # regular (non-editable) install — most robust
.venv/bin/pip install pytest httpx   # only if you want to run the tests

# one-shot download → ./data/downloads (or set XDL_DOWNLOAD_DIR=~/Downloads)
.venv/bin/xdl get "https://x.com/SpaceX/status/1732824684683784516"
.venv/bin/xdl ls                 # inspect the ledger
.venv/bin/xdl ls --status dead   # failures
.venv/bin/xdl redrive            # re-run every dead job
```

`ffmpeg` is required (HLS merge / MP4 remux): `brew install ffmpeg`.

> A plain `pip install .` copies the package into the venv and Just Works. An
> editable install (`pip install -e .`) is nicer for development but relies on `.pth`
> path injection, which some Python setups don't honor — see Troubleshooting below.
> After editing source under a non-editable install, re-run `pip install .`.
> (Tests don't need any install: `pytest` reads `src/` directly.)

Run the HTTP engine (for the iOS clients, or a hotkey on macOS):

```bash
.venv/bin/xdl serve              # uvicorn + in-process Huey consumer on :8080
curl localhost:8080/healthz
```

> **Troubleshooting — `ModuleNotFoundError: No module named 'xdl'` after install.**
> This only happens with *editable* installs (`pip install -e .`). Editable installs
> put `src/` on the path via a `.pth` file, and some Python setups (Anaconda/Miniconda
> interpreters, or any environment with a `sitecustomize` that rewrites `sys.path`)
> silently don't apply it — so the `xdl` entry point can't import the package. Fix:
> use a plain **`pip install .`** (copies the package into the venv, no `.pth`
> involved). That's why the Quickstart uses it.

## HTTP API

| Endpoint | Purpose |
|---|---|
| `POST /jobs` | `{"urls":[…],"mode":"async"\|"sync","dest":"downloads"\|"photos"}`. Each URL is an independent job. `sync` returns the MP4 bytes; on failure it auto-queues and returns 202 `queued`. |
| `GET /jobs?status=…` | Inspect queue / failures. |
| `GET /completed` | iOS collector pulls finished-but-uncollected Photos files. |
| `GET /files/{id}` | Download a finished file. |
| `POST /completed/{id}/ack` | Mark a file collected (so it isn't re-saved). |
| `POST /failures/redrive` | Re-enqueue every `dead` job. |
| `GET /healthz` | Liveness. |

## Configuration

Every path and knob is an env var — copy [`.env.example`](.env.example) and edit. All
state defaults under `./data/`. Key ones: `XDL_DB`, `XDL_ARCHIVE`, `XDL_DOWNLOAD_DIR`,
`XDL_STAGING_DIR`, `XDL_COOKIES` (optional burner cookies), `XDL_MAX_ATTEMPTS`,
`XDL_RETRY_DELAY`, `XDL_WORKERS`, `XDL_SHARED_SECRET`.

## Deploy

- **macOS client** (CLI / launchd daemon / hotkey): [deploy/macos-client.md](deploy/macos-client.md)
- **Always-on engine on Oracle Cloud (free)**: [deploy/oracle-setup.md](deploy/oracle-setup.md)
- **Networking with Tailscale** (nothing exposed publicly): [deploy/tailscale-setup.md](deploy/tailscale-setup.md)
- **iOS Shortcuts** (Tier 1 sync + Tier 2 collector): [ios/README.md](ios/README.md)
- **Docker**: `cd deploy && docker compose up -d --build`

## Tests

```bash
.venv/bin/pytest        # 30 tests; network-free (yt-dlp is mocked)
```

## Auth & account safety

Anonymous-first means most downloads use no account. Use a **burner** X account for the
cookie fallback (export cookies once with the "Get cookies.txt LOCALLY" extension;
never `--username/--password`). Treat the cookie file as a credential (`chmod 600`,
never commit it). Refresh cookies when auth-error `dead` jobs start appearing.
Automated access technically violates X's ToS; you own how downloaded content is used.

## Plan

The full design rationale lives in [`docs/PLAN.md`](docs/PLAN.md) (the implementation
plan this repo executes), including the now-vs-future migration playbook.

## License

MIT — see [LICENSE](LICENSE).
