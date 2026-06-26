# iOS client (iPhone 13 Pro Max)

iOS can't be shipped a binary `.shortcut` from here, so this is a build guide. Each
shortcut is a handful of actions in the **Shortcuts** app. Replace
`xdl-engine.<tailnet>.ts.net` with your real MagicDNS name (Tailscale admin console).

> iOS can't write to Photos from an unattended background event, so delivery is
> tiered: **Tier 1** (synchronous) for one-offs, **Tier 2** (async + collector) for
> batches/background.

Base URL used below: `http://xdl-engine.<tailnet>.ts.net:8080`
If you set `XDL_SHARED_SECRET`, add header `X-XDL-Token: <value>` to every request.

---

## Tier 1 — "Download X Video" (synchronous share-sheet shortcut)

The shortcut waits a few seconds for the engine to return the MP4, then saves it to
Photos. On failure the engine auto-queues it and returns `{"status":"queued"}` (HTTP
202), so nothing is lost — it'll appear later via the Collector.

**Actions:**

1. **Receive** *URLs* (and *Text*) from **Share Sheet** (the shortcut's header settings →
   "Use with Share Sheet", accept URLs).
2. **Get Contents of URL**
   - URL: `http://xdl-engine.<tailnet>.ts.net:8080/jobs`
   - Method: **POST**
   - Request Body: **JSON**
     ```json
     { "urls": ["Shortcut Input"], "mode": "sync", "dest": "photos" }
     ```
     (`urls` is an **Array** with one item = the **Shortcut Input** variable.)
   - Headers: `Content-Type: application/json`
3. **Save to Photo Album** ← the output of step 2.

That's the whole happy path. When the response is the video, it saves; when the post
needed queueing, step 3 simply has nothing to save and the file arrives via Tier 2.

**Optional robust branch** (handles the queued case explicitly instead of silently):
insert between 2 and 3 —
- **Get Dictionary Value** `status` from *Contents of URL*
- **If** *Dictionary Value* **is** `queued` → **Show Notification** "Queued — will appear
  via Collect" ; **Otherwise** → **Save to Photo Album** *Contents of URL*.

  > Test this branch on your iOS version: behavior of "Get Dictionary Value" on a
  > non-JSON (binary) response varies. If it errors, use the simple 3-action version.

Then: **Add to Home Screen / Share Sheet**. Trigger it from the X app's share → "Download
X Video".

---

## Tier 2 — "Collect to Photos" (pulls finished async downloads)

Use `mode:async` for batches/large files: a thin share shortcut POSTs and returns
instantly (engine downloads into staging); this Collector saves the finished files.

### 2a. Async share shortcut ("Queue X Video")
Same as Tier 1 step 1–2 but body `{"urls":["Shortcut Input"],"mode":"async","dest":"photos"}`,
and **no** Save step. It returns immediately.

### 2b. Collector shortcut
1. **Get Contents of URL** → `GET http://xdl-engine.<tailnet>.ts.net:8080/completed`
2. **Get Dictionary Value** `completed` from step 1 → a list.
3. **Repeat with Each** item in that list:
   1. **Text**: `http://xdl-engine.<tailnet>.ts.net:8080` + (Get Dictionary Value
      `download_url` of *Repeat Item*) → full file URL.
   2. **Get Contents of URL** → `GET` that full URL (the MP4).
   3. **Save to Photo Album** ← step 3.2 output.
   4. **Get Dictionary Value** `id` of *Repeat Item*.
   5. **Get Contents of URL** → `POST`
      `http://xdl-engine.<tailnet>.ts.net:8080/completed/{id}/ack`
      (build the URL with the id from 3.4). This marks it collected so it isn't re-saved.

### Running the collector hands-free
- **Manual**: tap it.
- **Time-of-day Personal Automation** (Shortcuts → Automation): run the Collector a few
  times a day. Recent iOS runs these with **no confirmation prompt**, so it's fully
  hands-free.
- **Pushcut** (optional): have the engine hit a Pushcut webhook on completion → a
  notification → one tap runs the Collector the instant a download finishes. Pushcut's
  free tier covers webhook-triggered shortcuts (with limits).

---

## Files instead of Photos (optional)
If saving to **Files** (iCloud Drive) were ever acceptable, point the shortcut's save
action at an iCloud Drive folder and the Collector disappears entirely — Photos is
sandboxed, which is the only reason the Collector exists.
