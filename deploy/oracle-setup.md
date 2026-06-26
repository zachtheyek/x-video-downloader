# Hosting on Oracle Cloud Always Free (the always-on engine, "now" tier)

The iPhone needs a 24/7 reachable engine; a sleeping laptop can't be it. Oracle's
Always Free tier (4 ARM Ampere A1 cores / 24 GB / 200 GB, $0/mo) is the canonical
always-on host until the home stack exists. The same Docker image moves to the home
box later with no code change.

## 1. Create the instance

1. Sign up at <https://cloud.oracle.com>. **Pick Singapore or Tokyo as the home
   region** — Always Free resources are pinned to the home region, and SG is lowest
   latency from KL. (This can't be changed later.)
2. Create an **Ampere A1 Compute** instance from the free pool: shape `VM.Standard.A1.Flex`,
   e.g. 2 OCPU / 12 GB (or the full 4/24), **Ubuntu 24.04 (aarch64)**. Add your SSH key.
3. **ARM capacity is often "out of host capacity."** The standard workaround is a
   retry loop on instance creation — re-issue the launch every ~60 s until it's granted
   (the OCI CLI `oci compute instance launch` in a `while` loop, or a community script).
4. **Do NOT add an ingress rule for 8080** (or any app port) in the VCN security list.
   Tailscale needs no inbound rules; this keeps the box off the public internet.

## 2. Keep it from being reclaimed

Idle Always Free instances get reclaimed. The resident engine (`restart: unless-stopped`)
plus normal use usually suffices, but to be safe add a light heartbeat cron:

```bash
( crontab -l 2>/dev/null; echo "*/15 * * * * cat /proc/loadavg >/dev/null" ) | crontab -
```

## 3. Install Docker + deploy

```bash
# Docker engine + compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Get the code (clone your repo, or scp the dir up)
git clone https://github.com/zachtheyek/x-video-downloader.git
cd x-video-downloader/deploy

# (optional) burner cookies — anonymous-first means this is only a fallback
mkdir -p secrets && chmod 700 secrets
#   copy cookies.txt into secrets/, chmod 600, and uncomment the cookie lines
#   in docker-compose.yml

docker compose up -d --build
docker compose logs -f          # watch it boot
curl -s localhost:8080/healthz  # {"ok":true,...}
```

State persists in `deploy/state/` (the `/data` volume): ledger, archive, downloads,
staging. Back this up; it's the migration payload for the home stack.

## 4. Wire networking

Follow [tailscale-setup.md](tailscale-setup.md) to join this box to your tailnet and
rename it `xdl-engine`. The iPhone then POSTs to
`http://xdl-engine.<your-tailnet>.ts.net:8080/jobs`.
