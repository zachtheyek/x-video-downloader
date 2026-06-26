# macOS client

The Mac runs the engine **locally** (no cloud round-trip): files write straight to
`~/Downloads`. Two ways to use it.

## Option A — CLI one-shots (no daemon)

```bash
xdl get "https://x.com/SpaceX/status/1732824684683784516"
xdl get <url1> <url2> ...           # multiple, sequential
xdl ls                              # ledger
xdl ls --status dead                # failures
xdl redrive                         # re-run every dead job
```

`xdl` is installed in the project venv (`.venv/bin/xdl`). Add a shell alias so it's
on your PATH from anywhere — append to `~/.zshrc`:

```bash
alias xdl="$HOME/Documents/Projects/in_progress/x-video-downloader/.venv/bin/xdl"
```

## Option B — always-on local engine (launchd) + hotkey

Keeps the engine resident on `localhost:8080` whenever you're logged in, giving
background + concurrent downloads.

1. Install the LaunchAgent (the sed fills in your paths):

   ```bash
   cd "$HOME/Documents/Projects/in_progress/x-video-downloader"
   mkdir -p "$HOME/.local/share/xdl"
   sed -e "s|__PROJECT_DIR__|$PWD|g" -e "s|__HOME__|$HOME|g" \
       deploy/com.user.xdl.plist > "$HOME/Library/LaunchAgents/com.user.xdl.plist"
   launchctl load "$HOME/Library/LaunchAgents/com.user.xdl.plist"
   curl -s localhost:8080/healthz   # {"ok":true,...}
   ```

2. Terminal-native trigger — append to `~/.zshrc`:

   ```bash
   xdlq() { curl -s localhost:8080/jobs -H 'content-type: application/json' \
            -d "{\"urls\":[\"$*\"],\"mode\":\"async\",\"dest\":\"downloads\"}"; }
   ```

   `xdlq <url>` enqueues; the file appears in `~/Downloads`.

3. Clipboard → hotkey (closest analog to the iOS flow): make a **macOS Shortcut**
   that reads the clipboard and does *Get Contents of URL* → POST
   `http://localhost:8080/jobs` with body
   `{"urls":["<Clipboard>"],"mode":"async","dest":"downloads"}`. Bind it to a hotkey
   via Raycast or the Shortcuts menu-bar item.

   A clipboard-watcher LaunchAgent is possible but **not recommended** — it fires on
   unintended copies. A deliberate hotkey is cleaner.

Uninstall the daemon: `launchctl unload "$HOME/Library/LaunchAgents/com.user.xdl.plist"`.
