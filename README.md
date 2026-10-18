# Aletheia

Aletheia builds open course readers for writing classes. An instructor picks a focus (first-year composition, technical communication, and others), starts from a sample course arc or a blank course, fits it to a 16-, 10-, 8-, or 5-week term, checks off openly licensed readings and assignments, and exports:

- a course reader (HTML or PDF) with a title/author/source/license credit for every item, and
- a Common Cartridge package (.imscc) that imports into Canvas, Moodle, D2L, Blackboard, and Sakai as modules, pages, and assignment shells.

Aletheia only redistributes what licenses allow. Every item carries one of four reuse tiers (adapt, adapt noncommercial, use whole and unchanged, link only), and items whose licenses haven't been verified are treated as link-only.

**Live:** https://aletheia-oer.fly.dev

**Status:** early development (October 2026). The catalog holds about 2,200 openly licensed readings and 620 assignments in rhetoric and composition. The reader includes the full text of each redistributable reading in the content cache (see below) and links to the rest, with full attribution for both.

## Layout

| Path | What's there |
|---|---|
| `corpus/rhetcomp/` | The rhetoric and composition **corpus pack**: `pack.yaml` (focuses, reuse tiers, term presets), catalog records as JSON Lines, course outcomes, and sample arcs. A new discipline supplies its own pack with the same layout. |
| `corpus/files/` | About 300 openly licensed PDFs, redistributed unchanged: `drive/` (from the maintainer's collection) and `web/` (mirrored from publishers). Each folder's `manifest.json` lists title, author, source, license, and checksum; `ATTRIBUTIONS.md` is the human-readable credit list. Only files listed in a manifest can be committed. |
| `backend/` | Django + django-ninja API: catalog, term-scaling planner, builds, reader and cartridge exports. |
| `frontend/` | React + TypeScript (Vite) app. |
| `research/` | How the catalog was built: raw research records, link check results, cleanup overrides, infrastructure notes. |
| `tools/` | Scripts: merge research into the catalog, export the corpus pack, check links, mirror PDFs. |

## Run it locally

Backend (Python 3.12):

```
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py load_pack rhetcomp
.venv\Scripts\python manage.py warm_content          # optional: full text for the reader, about an hour
.venv\Scripts\python manage.py runserver 8000
```

Frontend (Node 20+), in a second terminal:

```
cd frontend
npm install
npm run dev
```

Tests: `cd backend && .venv\Scripts\python -m pytest`.

PDF export uses WeasyPrint, which needs Pango; it works in the Docker image and on Linux/macOS with Pango installed.

## Full text

Exports take full text from a content cache in `backend/content_cache/`, which git ignores and the Docker image leaves out (set `ALETHEIA_CONTENT_CACHE` to move it; on Fly it sits on a volume, see Deploy). `manage.py warm_content` fills it for every reading the sample arcs use:

- Web chapters come through the publisher's API (Pressbooks, WordPress) or LibreTexts' own page markup, then get sanitized: scripts, styles, navigation and forms go; headings, lists, tables, figures and alt text stay.
- PDFs are copied from `corpus/files/` or any folder passed with `--pdf-dir`. A PDF that a `corpus/files` manifest lists but the disk doesn't have (as on the server) is fetched from `ALETHEIA_PDF_BASE_URL` and checked against the manifest's size and SHA-256. Anything else is downloaded from the publisher.
- The command follows robots.txt and stops at any bot check. WAC Clearinghouse PDFs sit behind one, so they have to come from a folder: `needs_pdf.csv` in the cache lists each one with the file name to save it under.

Link-only readings are never copied. NoDerivatives readings go in whole or not at all. `_report.json` in the cache says what was cached and why the rest wasn't.

The PDF reader comes in two versions. The accessible one (the default) is a single tagged document for screen readers: it carries the web text in full and gives readings that exist only as PDFs a credit line and a link. The print one merges the publishers' PDFs in page for page, with nothing added to them; pypdf can't merge tag trees, so it isn't tagged.

## Deploy

The `Dockerfile` builds the React app and serves it from Django. `fly.toml` deploys to Fly.io (app `aletheia-oer`, region `sjc`, one shared-cpu-1x machine with 1 GB that stops when idle). The database is a small Fly Postgres cluster (`aletheia-oer-db`) attached to the app, which sets `DATABASE_URL`; `DJANGO_SECRET_KEY` is a Fly secret. Deploy with `flyctl deploy --remote-only`.

Each deploy runs a release command in a temporary machine: `migrate`, then `createcachetable` (the database table the rate limits count in), then `load_pack rhetcomp`.

### Content cache volume

The full-text cache lives on a Fly volume named `aletheia_cache`, mounted at `/data`; `ALETHEIA_CONTENT_CACHE` is `/data/content_cache`. A volume belongs to one machine, so create it before the first deploy with the `[mounts]` section, and one more for each machine added later:

```
flyctl volumes create aletheia_cache --region sjc --size 1 -a aletheia-oer
```

Until it's filled, exports link to every reading and the rest of the site works as usual.

To fill it from a checkout whose `backend/content_cache` is already warm (a few minutes):

```
powershell -ExecutionPolicy Bypass -File tools\push_cache.ps1
```

The script tars the folder, uploads it with `flyctl ssh sftp put`, and unpacks it on the volume with `flyctl ssh console -C`. It requests `/healthz` every 20 seconds so the machine doesn't stop mid-upload. `-Replace` clears the volume's cache first, `-CachePath` sends a different folder, and `-DryRun` prints the flyctl commands without running them.

To warm it on the server instead (about two hours): the image has the `corpus/files` manifests but not the PDFs, so `warm_content` fetches each corpus PDF from the public repo (`ALETHEIA_PDF_BASE_URL`, GitHub's raw files) and the rest from publishers, as it does locally. Fly stops idle machines, and ssh sessions don't count as traffic, so set `min_machines_running = 1` in `fly.toml` and deploy before you start; set it back to 0 and deploy again when the run is done. Then:

```
flyctl ssh console -a aletheia-oer -C "python manage.py warm_content"
```

To keep the run going if the connection drops, start it in the background and check the log later:

```
flyctl ssh console -a aletheia-oer -C "sh -c 'nohup python manage.py warm_content > /data/warm.log 2>&1 &'"
flyctl ssh console -a aletheia-oer -C "tail -n 5 /data/warm.log"
```

### Limits

The API counts requests per client IP in fixed one-hour windows and answers 429, with `Retry-After`, past the limit: 30 new saved courses, 300 saves, 20 PDF exports, 60 other exports, 300 plans, 20 program changes, 600 other writes, and 2,000 reads. Request bodies over 512 KB get 413. Each limit is a setting with an environment override (`ALETHEIA_RATE_EXPORT_PDF=40`, for example; see `aletheia/settings.py`), and `ALETHEIA_RATE_LIMIT=0` turns them all off. A campus behind one NAT address shares one count, so raise the limits if a workshop runs into them.

### Admin

The Django admin at `/admin/` needs a superuser. Create one from a terminal (the prompts need `--pty`):

```
flyctl ssh console --pty -a aletheia-oer -C "python manage.py createsuperuser"
```

### Old saved courses

`prune_builds` deletes saved courses nobody has updated in 540 days (about 18 months). Courses linked to a program are always kept. Check the list first:

```
flyctl ssh console -a aletheia-oer -C "python manage.py prune_builds --dry-run"
flyctl ssh console -a aletheia-oer -C "python manage.py prune_builds"
```

`--older-than-days` changes the cutoff.

## Licenses

- Code: MIT (see `LICENSE`).
- Catalog records, summaries, and arc text: CC BY 4.0 (see `corpus/LICENSE-DATA.md`). The works the catalog describes keep their own licenses.
