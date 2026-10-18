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

Exports take full text from a content cache in `backend/content_cache/`, which git ignores and the Docker image leaves out (set `ALETHEIA_CONTENT_CACHE` to move it). `manage.py warm_content` fills it for every reading the sample arcs use:

- Web chapters come through the publisher's API (Pressbooks, WordPress) or LibreTexts' own page markup, then get sanitized: scripts, styles, navigation and forms go; headings, lists, tables, figures and alt text stay.
- PDFs are copied from `corpus/files/` or any folder passed with `--pdf-dir`, or downloaded.
- The command follows robots.txt and stops at any bot check. WAC Clearinghouse PDFs sit behind one, so they have to come from a folder: `needs_pdf.csv` in the cache lists each one with the file name to save it under.

Link-only readings are never copied. NoDerivatives readings go in whole or not at all, and their PDFs are merged into the PDF reader page for page, with nothing added to them. `_report.json` in the cache says what was cached and why the rest wasn't.

## Deploy

The `Dockerfile` builds the React app and serves it from Django. `fly.toml` deploys to Fly.io (app `aletheia-oer`, region `sjc`); each deploy runs migrations and reloads the corpus pack. The database is a small Fly Postgres cluster (`aletheia-oer-db`) attached to the app, which sets `DATABASE_URL`; `DJANGO_SECRET_KEY` is a Fly secret. Deploy with `flyctl deploy --remote-only`.

## Licenses

- Code: MIT (see `LICENSE`).
- Catalog records, summaries, and arc text: CC BY 4.0 (see `corpus/LICENSE-DATA.md`). The works the catalog describes keep their own licenses.
