# Deploy — Fly.io backend + Cloudflare edge

Backend (FastAPI + native DuckDB) runs on **Fly.io** with a persistent volume
(durable SQLite — skills/mappings survive restarts). **Cloudflare** serves the
static frontend (Pages), DNS, TLS, and your custom domain. Raw files stay on
the Fly volume by default; flip to R2 later with one env var (no code change).

You run the authenticated commands (browser logins). Nothing here needs Docker
locally — Fly builds the image with a remote builder.

---

## 0. One-time CLI setup

```bash
# Fly
curl -L https://fly.io/install.sh | sh      # or: brew install flyctl
fly auth login                               # opens browser

# Cloudflare
npx wrangler login                             # opens browser
```

In Claude Code, run interactive logins with the `!` prefix, e.g.
`! fly auth login`, so the browser flow runs in your session.

---

## 1. Backend → Fly.io

> **Running these via the `!` prefix in Claude Code:** each `!` line is a
> *fresh shell* — a `cd` does **not** carry to the next line. So every
> command below is made cwd-independent: `fly launch`/`fly deploy` are run
> as `cd api && …` (they need the Dockerfile + fly.toml build context),
> and the rest pass `-a spreadsheet-agent-api` explicitly. In a normal
> persistent terminal you can instead just `cd api` once and drop the
> `cd api &&` / `-a` repetition.

```bash
# Create the app WITHOUT deploying (so we can add a volume + secrets first).
cd api && fly launch --no-deploy --copy-config \
  --name spreadsheet-agent-api --region sin --yes

# Persistent volume the metadata DB + raw files live on (matches fly.toml
# mount "ssa_data" -> /data). 3GB is plenty for a first client.
fly volumes create ssa_data -a spreadsheet-agent-api --region sin --size 3

# Secrets (never put these in fly.toml). OPENAI is optional — without it
# mapping uses the bilingual heuristic and NL-query returns a sample.
fly secrets set OPENAI_API_KEY=sk-... -a spreadsheet-agent-api   # optional
# fly secrets set OPENAI_MODEL=gpt-4o-mini -a spreadsheet-agent-api

cd api && fly deploy

fly status -a spreadsheet-agent-api    # hostname: spreadsheet-agent-api.fly.dev
curl https://spreadsheet-agent-api.fly.dev/health   # {"status":"ok",...}
```

Verify the full critical path against the **live** backend (opt-in HTTP
smoke — ingest → map → confirm → query → save skill → apply → remap):

```bash
cd api && python -m tests.prod_smoke https://spreadsheet-agent-api.fly.dev
```

It prints a `--check-skill <name>` command; run that after the restart in
§4 to prove the saved skill survived (durable-volume proof).

---

## 2. Backend custom domain (api.excel-agents.com)

In Cloudflare → your domain → **DNS** → add:

| Type  | Name | Target                          | Proxy        |
|-------|------|---------------------------------|--------------|
| CNAME | api  | spreadsheet-agent-api.fly.dev   | DNS only (grey) |

Grey-cloud (DNS only) so **Fly** terminates TLS. Then issue the cert:

```bash
fly certs add api.excel-agents.com
fly certs show api.excel-agents.com     # wait until "Issued"
curl https://api.excel-agents.com/health
```

---

## 3. Frontend → Cloudflare Pages (static)

The site is a static export (`web/out/`, already verified). Direct upload is
fastest for a first rollout (no GitHub repo required).

```bash
cd web

# Create the Pages project once.
npx wrangler pages project create spreadsheet-agent --production-branch=main

# Build with the API URL inlined (NEXT_PUBLIC_* is baked at build time).
NEXT_PUBLIC_API_BASE=https://api.excel-agents.com npm run build

# Deploy. --branch=main is REQUIRED or it goes to a throwaway preview URL.
npx wrangler pages deploy out --project-name=spreadsheet-agent --branch=main
```

Add the frontend custom domain: Cloudflare → Workers & Pages →
`spreadsheet-agent` (Pages) → **Custom domains** → add the apex
`excel-agents.com` (or a subdomain like `app.excel-agents.com` if you
prefer). Cloudflare creates the DNS records + cert automatically; apex
works because Cloudflare flattens the CNAME on the authoritative side.

---

## 4. Close the CORS loop

The backend must allow the final frontend origin. Update and redeploy:

```bash
fly secrets set CORS_ORIGINS=https://excel-agents.com -a spreadsheet-agent-api
# (multiple allowed, comma-separated: "https://excel-agents.com,https://spreadsheet-agent.pages.dev")
cd api && fly deploy
```

Prove durability with the smoke script's two-phase mode. The first run
saves a timestamped skill and prints the exact re-check command:

```bash
cd api && python -m tests.prod_smoke https://api.excel-agents.com
fly apps restart spreadsheet-agent-api -a spreadsheet-agent-api
# paste the printed command, e.g.:
python -m tests.prod_smoke https://api.excel-agents.com --check-skill prod-smoke-1747...
# [PASS] skill present on live backend  -> the volume persisted it
```

(Or do it by hand: open `https://excel-agents.com`, switch the UI to
中文, upload a Chinese CSV → confirm mapping → save a skill, restart, and
confirm the skill is still listed.)

---

## Later (optional, no code change)

- **Raw files → R2** for object-storage durability/scale: create an R2
  bucket + S3 API token, then
  `fly secrets set STORAGE_BACKEND=r2 R2_ENDPOINT=<acct>.r2.cloudflarestorage.com R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=spreadsheet-agent`
  and `fly deploy`. DuckDB streams CSV straight from R2 via httpfs
  (`api/app/storage.py`, `duck.py`).
- **Git-connected Pages CI** instead of manual upload: push the repo to
  GitHub, connect it in the Pages dashboard, root dir `web`, build
  `npm install && npm run build`, deploy
  `npx wrangler pages deploy out --project-name=spreadsheet-agent --branch=main`,
  env `NEXT_PUBLIC_API_BASE=https://api.excel-agents.com`.

## Cost (first client, scale-to-zero)

Fly: ~$0 idle (scale-to-zero) + ~$0.15/GB-mo volume (3GB ≈ $0.45/mo) +
minutes of compute when used. Cloudflare Pages/DNS/R2 free tier covers this.
