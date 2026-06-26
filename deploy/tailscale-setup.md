# Networking — Tailscale (mesh VPN, nothing public)

Tailscale is a WireGuard mesh VPN. Install it on each device, sign in with one
identity, and they form a private "tailnet": stable `100.x` IPs + MagicDNS names,
peer-to-peer and encrypted, **no port-forwarding and nothing exposed to the public
internet**. Because the network layer already ensures only your devices reach the
engine, **no app-level auth is required** (the `XDL_SHARED_SECRET` header is optional).

## Steps

1. **Account** — create a free **Personal** tailnet at <https://tailscale.com>
   (sign in with Google/GitHub/Apple). Free tier: up to 100 devices.

2. **Oracle box** (Ubuntu ARM):
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   Open the printed URL to authenticate. Then in the admin console:
   - **Rename the node to `xdl-engine`** so its MagicDNS name is stable.
   - **Disable key expiry** for this node (else it drops off the tailnet every
     ~180 days — bad for an always-on server).
   - `tailscale ip -4` shows its `100.x` address.

3. **iPhone** — install the Tailscale app, sign in with the same identity. MagicDNS
   is on by default, so names resolve.

4. **Mac** — `brew install --cask tailscale` (or the app), sign in. Now Mac, iPhone,
   and the Oracle box all see each other.

5. **Address from the shortcut** — POST to
   `http://xdl-engine.<your-tailnet>.ts.net:8080/jobs`
   (find the exact `.ts.net` suffix in the admin console). This stable name survives
   the box's public IP changing **and** migration to the home box (just reassign the
   hostname — the iPhone shortcut never changes).

6. **Lock down the port** (defense in depth on top of "no inbound cloud rule"):
   ```bash
   # allow 8080 only from the tailnet CGNAT range
   sudo ufw allow from 100.64.0.0/10 to any port 8080 proto tcp
   sudo ufw enable
   ```
   Plain HTTP over the tailnet is already WireGuard-encrypted; TLS is optional
   (`tailscale serve` / `tailscale cert` can issue real certs for MagicDNS names).

7. **ACLs / tags** (optional, later) — the default (open within your tailnet) is fine
   for personal use. Tag the engine host when the home stack joins if you want finer
   scoping.

## Migrating to the home stack

When the Mac mini/NAS/Pi exist: stand the same image up on the home box, point the
`/data` volume at a NAS mount, then **reassign the `xdl-engine` hostname** to the home
box. The iPhone shortcut is untouched. Retire Oracle or keep it as an off-site fallback.
