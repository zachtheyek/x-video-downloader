# X Video Downloader — Implementation Plan

*Self-contained reference for later implementation. Verified against tooling state as of June 2026.*

> **Implementation note (as built).** This repo executes the plan below. One
> correction was applied during implementation: the yt-dlp Python-API key in §2.1 is
> **`remuxvideo`**, not `remux_video` (the latter is silently ignored). Verified against
> the installed yt-dlp via `--remux-video → dest remuxvideo`. The engine also adds two
> ledger columns the iOS collector needs (`dest`, `collected`), short-circuits
> permanent errors (deleted/unsupported posts) straight to `dead` instead of retrying,
> and has sync one-offs bypass the dedup archive so they always return bytes. Phases 1–2
> (engine + Oracle/Tailscale/iOS deployment docs) are built and tested; phases 3–4 are
> scaffolded.

---

## 0. Scope and locked decisions

**Goal.** Paste an X post link (from the iOS share sheet, or the clipboard on macOS) and have the highest-quality video download in the background, landing in **Photos** on iPhone or **`~/Downloads`** on macOS. Multiple concurrent downloads, sensible defaults, automatic retries (3 attempts, 60 s apart), and a persistent ledger of failed links for easy re-drive.

**Locked decisions:**

1. **No always-on machine now.** Future home stack: Mac mini/Studio + NAS + Raspberry Pi cluster. Build for the current setup; keep migration to the home stack a configuration change, not a rewrite.
2. **Tailscale** for connectivity (mesh VPN; nothing exposed publicly).
3. **Both** iOS delivery tiers: synchronous (lands in Photos immediately) for one-offs, and async + collector for batches/background.
4. **Burner X account** for the cookie auth-fallback path; main Premium account stays out of the pipeline (see §7).

**Mobile target:** iPhone 13 Pro Max (iOS).

---

## 1. Architecture

### 1.1 One engine, two thin clients

The platforms differ only in *trigger* (share sheet vs. clipboard/CLI) and *delivery* (Photos vs. `~/Downloads`). Everything hard — extraction, concurrency, retries, the failed-link ledger, dedup — lives in one **engine**. Clients capture a URL, POST it, and handle local delivery.

### 1.2 Now vs. future

Because the Mac is a laptop that sleeps, it cannot be what the iPhone talks to (mobile is the primary use case and needs 24/7 reachability). The always-on role goes to Oracle now and migrates to the home stack later.

**Now — two deployments of the same package:**

- **Oracle Cloud Always Free instance** = canonical always-on service. Runs the engine in **server mode** (FastAPI + queue). Serves the iPhone from anywhere; holds the authoritative ledger, download archive, and iOS staging directory.
- **Mac** = **local mode** (launchd service bound to localhost, or CLI one-shots). Desktop downloads go straight to `~/Downloads` with no cloud round-trip.

**Future — one deployment on the home stack:**

- A single always-on engine on the Mac mini/Studio or a Pi node replaces Oracle. Both Mac and iPhone point at it.
- The **NAS** holds the single canonical ledger + archive + staging (NFS/SMB export).
- Oracle is retired or kept as an off-site fallback.
- The Pi cluster's role is always-on *hosting*, not compute. This workload never needs cluster-scale parallelism. If desired, the queue can be moved to Redis and workers spread across nodes — optional, not required.

| Concern | Now | Future (home stack) |
|---|---|---|
| Always-on engine (serves iPhone) | Oracle Always Free (Singapore region), Docker | Mac mini/Studio or Pi node, same image |
| Desktop downloads | Mac-local instance → `~/Downloads` | Mac → home engine (or keep local) |
| Canonical state (ledger/archive/staging) | local, env-configured paths | NAS export, single shared ledger |
| Concurrency | huey + SQLite, N workers, one box | same; optional huey+Redis, workers across Pi nodes |
| Client addressing | Tailscale MagicDNS name → Oracle | reassign same name → home box (clients unchanged) |

### 1.3 The three flexibility levers (migration = config, not code)

1. **Code shape.** A `core` library (extraction + retry + ledger I/O) with two thin entrypoints: `cli` (local/launchd) and `server` (FastAPI). Same core in both modes, on any arch — yt-dlp and ffmpeg are architecture-agnostic, so x86 Mac, ARM Oracle, and ARM Pi all run the same code.
2. **Externalised state.** Every path is an environment variable: `XDL_DB` (ledger), `XDL_ARCHIVE` (download-archive), `XDL_DOWNLOAD_DIR`, `XDL_STAGING_DIR`. "Move state to the NAS" becomes pointing these at mount points — no code change.
3. **Stable addressing.** Clients target a Tailscale MagicDNS name (`xdl-engine.<tailnet>.ts.net`), never an IP. Moving the engine host means reassigning that hostname; the iPhone shortcut is untouched.

Package the server as a Docker image so redeploying on the home box is `docker compose up` with changed volume paths.

```
xdl/
├── core/            # extraction, retry policy, ledger, config (env-driven paths)
├── cli.py           # `xdl get <url>` — local one-shot, writes ~/Downloads, local ledger
├── server.py        # FastAPI app + huey tasks
├── Dockerfile       # multi-arch (server mode)
├── docker-compose.yml
└── pyproject.toml
```

---

## 2. The download engine

### 2.1 Extraction (yt-dlp)

yt-dlp is the backbone (native X support, near-daily updates). Use the **Python API** (`yt_dlp.YoutubeDL`) inside the worker, not a subprocess, for structured error objects (to classify login-required vs. transient vs. dead) and progress hooks.

Canonical highest-quality options for X:

```python
ydl_opts = {
    "format": "bv*+ba/b",
    "format_sort": ["res", "vbr", "abr"],   # X already sorts by res then bitrate; explicit is robust
    "merge_output_format": "mp4",
    "remux_video": "mp4",                    # guarantee Photos-compatible H.264/AAC MP4 container
    "concurrent_fragment_downloads": 4,      # native -N; speeds HLS fragment fetches
    "retries": 10,                           # transient errors within one attempt
    "fragment_retries": 10,
    "retry_sleep_functions": {"http": lambda n: min(5, n)},
    "download_archive": os.environ["XDL_ARCHIVE"],
    "outtmpl": "%(uploader_id)s-%(id)s.%(ext)s",
}
```

Notes:
- **Do not transcode.** X video is almost always already H.264; re-encoding costs quality and time for nothing. `remux_video=mp4` only rewraps the container when needed (HLS → MP4).
- **Do not use aria2c for fragments.** aria2c's HLS/DASH support was removed in the June 2026 release; native `concurrent_fragment_downloads` (`-N`) is the supported path.
- ffmpeg must be present (HLS fragment merge / remux).

### 2.2 Authentication strategy (anonymous-first, cookie fallback)

X's anonymous guest-token path is the default and works for the large majority of public videos. Counterintuitively, **passing cookies can break X downloads** that work fine anonymously, so cookies are a fallback, not a default:

1. Attempt download **anonymously** (no cookies). Zero account exposure.
2. On a login-required / sensitive-content error, retry the *same* URL **with cookies** from the burner account.

Cookie handling:
- Export once with the **"Get cookies.txt LOCALLY"** browser extension (local-only; avoids the Chromium cookie-DB lock problem). Point `cookiefile` at the resulting Netscape-format file.
- Never use `--username/--password` — that triggers fresh bot-like logins and is what gets accounts flagged.
- Cookies expire over weeks; treat refresh as periodic maintenance. Because auth is a fallback, a stale cookie only degrades access to *gated* posts, not the common path — it fails gracefully. Alert when `dead` jobs with auth errors start appearing; that's the refresh signal.
- Rate-limit to mimic browsing (modest worker count, natural pacing, 60 s retry backoff).

### 2.3 Orchestration (FastAPI + Huey/SQLite)

Persistent queue with bounded concurrency and delayed retries, no Redis to run:

- **Huey backed by SQLite** (WAL mode). Its retry decorator maps exactly onto the spec: `@huey.task(retries=3, retry_delay=60)`.
- Concurrency = consumer worker count (start at 4). Per-download speed comes from `-N`.
- If write contention ever bites under heavy parallelism, swap to `huey` + Redis (or `arq` + Redis) — same interface, workers can then span Pi nodes.

**HTTP surface:**

| Endpoint | Purpose |
|---|---|
| `POST /jobs` | Body `{"urls": [...], "mode": "async"\|"sync", "dest": "downloads"\|"photos"}`. One or many URLs; each enqueued as an independent job so one bad link can't fail a batch. |
| `GET /jobs?status=...` | Inspect queue / failures. |
| `GET /completed` | iOS collector pulls finished-but-uncollected files. |
| `POST /completed/{id}/ack` | Mark a file collected (so it isn't re-saved). |
| `POST /failures/redrive` | Re-enqueue everything in the dead-letter state. |

**Sync mode behaviour (iOS Tier 1).** A sync request runs the download inline (anonymous → cookie fallback, one quick retry) and returns the file bytes so the share sheet can save immediately. *On failure it auto-enqueues to the async queue* (full 3×/60 s + ledger) and returns a `queued` status — so a failed sync download is never lost, lands in the ledger, and appears later via the collector. This unifies the two tiers gracefully and avoids hanging the share sheet through 60 s backoffs.

### 2.4 Retry layering

Three independent layers, by design:
- **yt-dlp internal** (`retries`/`fragment_retries`): transient network blips *within one attempt* (a dropped HLS fragment).
- **Auth fallback** (application logic): anonymous → cookies, on login-required errors.
- **Huey** (`retries=3, retry_delay=60`): whole-post failures (rate-limited, extractor hiccup), re-run a minute later.

### 2.5 Failed-link ledger (SQLite)

Queryable and re-drivable — the source of truth.

```sql
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  status TEXT NOT NULL,         -- queued|running|success|retrying|dead
  attempts INTEGER DEFAULT 0,
  last_error TEXT,
  output_path TEXT,
  created_at TEXT, updated_at TEXT
);
```

When Huey exhausts retries, the final handler sets `status='dead'`, stores `last_error`, and (optionally) appends the URL to `failed.txt` for a dead-simple artifact. "Return to failed links" = `SELECT url FROM jobs WHERE status='dead'`; re-try them all = `POST /failures/redrive`.

### 2.6 Dedup

`download_archive` (`XDL_ARCHIVE`) ensures re-drives and accidental re-shares never re-download a video you already have.

---

## 3. macOS client

Engine runs locally (no round-trip), so desktop is the easy half — files write directly to `~/Downloads`. Trigger options, lowest-friction first:

- **Shell function** (terminal-native):
  ```bash
  xdl() { curl -s localhost:8080/jobs -d "{\"urls\":[\"$*\"],\"dest\":\"downloads\"}"; }
  ```
- **macOS Shortcut** reading the clipboard → same endpoint, bound to a hotkey via Raycast or the Shortcuts menu-bar item (closest analog to the iOS flow).
- A clipboard-watcher LaunchAgent is possible but **not recommended** — it fires on unintended copies. A deliberate hotkey is cleaner.

Run the local engine as a launchd `LaunchAgent` (so it's up whenever you're logged in and gives background + concurrent downloads), or skip the daemon and use the CLI for synchronous one-shots.

---

## 4. iOS client (iPhone 13 Pro Max)

### 4.1 Trigger

A **Share Sheet shortcut** ("Download X Video"): accepts a URL from the X app's share → copy-link/share action, POSTs to `http://xdl-engine.<tailnet>.ts.net:8080/jobs`.

iOS cannot write to Photos from an unattended background event (no push-to-save), so delivery is tiered.

### 4.2 Tier 1 — synchronous (one-offs)

`mode=sync`. The shortcut waits for the engine to return the MP4 (a few seconds for typical X videos), then runs **Save to Photo Album**. The share sheet handles the wait; the video is in Photos before you put the phone down. On failure the engine auto-queues it (Tier 2) and returns `queued`, so nothing is lost.

**Shortcut actions:**
1. Receive shared URL (Share Sheet input).
2. Get Contents of URL → POST `/jobs`, JSON `{"urls":[url],"mode":"sync","dest":"photos"}`, expect a file response.
3. If response is a video file → **Save to Photo Album**.
4. If response is `{"status":"queued"}` → brief notification: "Large/failed — queued, will appear via Collect."

### 4.3 Tier 2 — async + collector (batches / background)

`mode=async`. The share shortcut POSTs and returns instantly; the engine downloads into `XDL_STAGING_DIR`. A separate **"Collect to Photos"** shortcut pulls finished files and saves them.

**Collector shortcut actions:**
1. Get Contents of `GET /completed` → list of `{id, download_url}`.
2. For each: Get Contents of `download_url` → **Save to Photo Album** → POST `/completed/{id}/ack`.

Run the collector by: (a) manual tap; (b) a **time-of-day Personal Automation** (recent iOS runs these with no confirmation prompt — a few sweeps a day pull everything in hands-free); or (c) **Pushcut**, push-triggered the instant a download finishes (engine hits a Pushcut webhook → notification → one tap runs the collector). Pushcut's free tier covers webhook-triggered shortcuts with limits.

### 4.4 Files instead of Photos (optional)

If Files were ever acceptable, writing to an iCloud Drive folder removes the collector entirely. Photos is sandboxed, so the collector is the cost of the Photos requirement.

---

## 5. Hosting — Oracle Cloud Always Free (now)

Oracle's Always Free tier gives 4 ARM (Ampere A1) cores / 24 GB RAM / 200 GB storage at $0/month — wildly over-spec for this. Two known frictions, both manageable:

- **Capacity for ARM instances is inconsistent.** The standard workaround is a provisioning script that retries instance creation every ~60 s until capacity is granted.
- **Idle Always Free instances get reclaimed.** Run a small heartbeat (e.g., a cron job that does light CPU work, or the engine itself staying resident) so it isn't flagged inactive.

**Setup outline:**
1. Sign up at cloud.oracle.com. **Choose Singapore (or Tokyo) as the home region** — Always Free resources are pinned to the home region, and Singapore is lowest-latency from KL.
2. Create an **Ampere A1 Compute** instance (allocate from the free 4 OCPU / 24 GB pool), Ubuntu 24.04 (ARM). Add your SSH key.
3. **Do not open ports 8080 (or any app port) in the VCN security list.** Tailscale needs no inbound rules (see §6) — this keeps the box off the public internet entirely.
4. Install Docker + compose plugin.
5. Deploy the engine image; mount volumes for `XDL_DB`, `XDL_ARCHIVE`, `XDL_DOWNLOAD_DIR`, `XDL_STAGING_DIR` (local dirs now; NAS mounts later). Copy the burner `cookies.txt` into the cookie volume.
6. Bind the FastAPI service to the Tailscale interface only (or `0.0.0.0` and rely on the absence of inbound firewall rules + Tailscale).

---

## 6. Networking — Tailscale (step-by-step)

**Concept.** Tailscale is a mesh VPN over WireGuard. Install on each device, sign in with one identity; they form a private "tailnet". Each device gets a stable `100.x` IP (CGNAT range) and a MagicDNS name (`device.<tailnet>.ts.net`) resolvable from your other devices. Devices connect peer-to-peer (encrypted), traversing NAT/firewalls with **no port-forwarding and nothing exposed to the public internet** — only devices on your tailnet can connect.

**Steps:**
1. Create a free **Personal** account at tailscale.com (sign in with Google/GitHub/Apple). Free tier covers up to 100 devices.
2. **Oracle box** (Ubuntu ARM):
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   Open the printed URL to authenticate → the box joins the tailnet. In the admin console, rename it to **`xdl-engine`** so its MagicDNS name is stable, and **disable key expiry** for this node (otherwise it drops off the tailnet every ~180 days — bad for an always-on server). `tailscale ip -4` shows its `100.x` address.
3. **iPhone:** install the Tailscale app, sign in with the same identity. MagicDNS is on by default → names resolve.
4. **Mac:** install Tailscale (`brew install --cask tailscale` or the app), sign in. Now Mac, iPhone, and the Oracle box all see each other.
5. **Addressing from the shortcut:** POST to `http://xdl-engine.<your-tailnet>.ts.net:8080/jobs` (find the exact `.ts.net` suffix in the admin console). Stable name → survives the box's public IP changing *and* survives migration to the home box (just reassign the hostname).
6. **Lock down the port:** bind FastAPI to the Tailscale interface, or add an OS firewall rule allowing 8080 only from `100.64.0.0/10`. Plain HTTP over the tailnet is already WireGuard-encrypted; TLS is optional (Tailscale `serve` / `tailscale cert` can issue real certs for MagicDNS names if you want it).
7. **ACLs / tags (optional, later):** Tailscale ACLs can restrict which devices reach which ports. Default (open within your tailnet) is fine for personal use; tag the engine host when the home stack joins if you want finer scoping.

Because the network layer (Tailscale) already ensures only your devices reach the engine, **no app-level auth is required**. A shared-secret header is optional belt-and-suspenders.

---

## 7. Auth, cookies, and account safety

- **Anonymous-first** means most downloads use no account at all.
- **Burner account** for the cookie fallback. Rationale: a documented case exists of X warning, suspending, then blocking an account used for automated yt-dlp downloads — but that used `--username/--password` (fresh bot-like logins) at high frequency. Your pattern (cookie-only, anonymous-first, low volume, paced) is far lower risk, but not zero. Since X video quality isn't Premium-gated, a burner yields identical results at zero functional cost. The asymmetry favours protecting the main account.
- **Rules:** cookie-export only (never password auth); rate-limit; refresh cookies when auth-error `dead` jobs appear.
- **Hygiene:** treat the cookie file as a credential (`chmod 600`, never in a repo or a tracked `.claude/`-style dir). Avoid `--downloader curl` and unsafe `--exec` conversions (recent CVEs).
- **ToS:** automated access technically violates X terms; you own how downloaded content is used. Downloading public posts you can already view is the same content the browser fetches.

---

## 8. Build phases

1. **MVP engine, desktop-only.** `core` + `cli`; the yt-dlp options from §2.1 with anonymous→cookie fallback; `POST /jobs`; the `jobs` table; `download_archive`. Drive with `xdl`. This alone gives retries, the ledger, and concurrency on macOS.
2. **iOS Tier 1.** Stand up the engine on Oracle (server mode) behind Tailscale. Build the synchronous Share Sheet shortcut → `mode=sync` → Save to Photo Album. Validates the full mobile path with the fewest moving parts.
3. **iOS Tier 2.** Add `XDL_STAGING_DIR`, `GET /completed` + ack, the Collect-to-Photos shortcut, and a scheduled Personal Automation (and/or Pushcut). Batches and large files now work background-style.
4. **Hardening.** `POST /failures/redrive`, stale-cookie alerting, optional ntfy/Pushcut completion push, optional cobalt backend behind the same `/jobs` interface for A/B.

---

## 9. Scaling to the home stack (migration playbook)

When the Mac mini/Studio + NAS + Pi cluster exist:

1. **Stand up the engine on the home box** — same Docker image, new host. Install Tailscale on it.
2. **Point state at the NAS** — set `XDL_DB`, `XDL_ARCHIVE`, `XDL_DOWNLOAD_DIR`, `XDL_STAGING_DIR` to NFS/SMB mounts. One canonical ledger now spans desktop and mobile. (Migrate the existing SQLite + archive from Oracle by copying the files to the NAS first.)
3. **Reassign the Tailscale name** — give the home box the `xdl-engine` hostname (or update the one config value in the shortcut). The iPhone shortcut is otherwise untouched.
4. **Point the Mac at the home engine** (or keep its local instance for offline-from-home use).
5. **Retire Oracle** or keep it as an off-site fallback / remote-access backup.
6. **Optional horizontal scale** — swap Huey/SQLite for Huey+Redis and run consumer workers across Pi nodes. Not needed for this workload; available if wanted.

The only code-level prerequisite for all of the above — the `core`/`cli`/`server` split, env-driven paths, and Tailscale-name addressing — is in place from phase 1, so this migration is configuration only.

---

## Sources (verified June 2026)

- yt-dlp X support, cookie auth, native `-N` (aria2c HLS removal): yt-dlp GitHub releases & docs.
- X account-suspension precedent for automated downloads: yt-dlp issue #10754.
- Cobalt self-hosting (stateless, `cookies.json`, API-key requirement): imputnet/cobalt docs.
- Oracle Cloud Always Free specs / reclaim behaviour / capacity-retry: provider docs + self-hosting community guides.
- Pushcut tiers / Automation Server: pushcut.io.
- Tailscale free Personal plan, MagicDNS, key expiry: tailscale.com docs.
