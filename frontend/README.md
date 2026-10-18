# Aletheia frontend

The React app for Aletheia: home, focus and sample-course pages, the course builder, the catalog, and the about page. It talks to the Django API in `../backend`.

Stack: Vite, React 18, TypeScript, react-router-dom. No UI framework or state library; styles are plain CSS in `src/styles.css` (light and dark themes follow `prefers-color-scheme`).

## Running it locally

You need two terminals, one for the API and one for Vite. From the project root:

**Terminal 1: Django API on port 8000**

```powershell
backend\.venv\Scripts\python.exe backend\manage.py runserver 8000
```

**Terminal 2: Vite dev server on port 5173**

```powershell
cd frontend
npm install   # first time only
npm run dev
```

Open http://localhost:5173. Vite proxies every `/api` request to http://127.0.0.1:8000, so there's no CORS setup.

If the sample courses don't show up, reload the corpus pack (this rebuilds the catalog tables from `corpus/rhetcomp/`):

```powershell
backend\.venv\Scripts\python.exe backend\manage.py load_pack rhetcomp
```

## Checks and builds

```powershell
npm run typecheck   # tsc --noEmit
npm run build       # typecheck, then build to frontend/dist
```

In production Django serves `frontend/dist` at the site root and sends every non-API path to `index.html`, so the app uses `BrowserRouter` routes. Django only picks up `dist/` for static files at startup (`WHITENOISE_ROOT`), so restart runserver after the first build if you want to try the built app on port 8000.

PDF export returns 501 on Windows because WeasyPrint isn't installed; the builder shows a note suggesting the HTML reader and the browser's Print to PDF instead.

## Layout

```
src/
  api.ts               typed API client, shared types, brief cache
  storage.ts           localStorage helpers (saved builds, drafts), all wrapped in try/catch
  hooks.ts             useAsync, useDebounced, pack context, focus and term-preset helpers
  App.tsx              header, routes, footer
  pages/               Home, FocusPage, ArcDetail, Catalog, About, NotFound
  builder/
    Builder.tsx        /build/new and /build/:id: loading, drafts, save, fork, re-plan
    model.ts           pure state helpers: plan to state, merge edits across a re-plan, blank units
    UnitCard.tsx       one unit: goals, reading-load meter, reading and assignment checklists
    TermBar.tsx        week grid, schedule list, dropped/merged note
    SidePanel.tsx      totals, license report (debounced), exports
    SearchDrawer.tsx   "Add reading" / "Add assignment" search
  components/          Drawer (native <dialog>), TierBadge, TermPicker, TopicChips, ItemDetail, status messages
```

## How the builder keeps work

- **Drafts.** Unsaved edits are written to localStorage (`aletheia:draft:new` for a new course, `aletheia:draft:<id>` for a saved one), so a refresh brings them back with a note and a way to discard them.
- **Saving.** The first save POSTs `/api/builds` and stores `{id: edit_token}` under `aletheia:builds`; later saves PUT with the `X-Edit-Token` header. A course opened without a token is view-only and offers "Make my own copy," which POSTs a fork.
- **Edit links.** The share box offers a private edit link of the form `/build/<id>#edit=<token>`. The token sits in the URL fragment, which browsers don't send to the server; opening the link stores the token in that browser.
- **Re-planning.** Changing the term of a sample-course build calls `/api/plan` again. Checked boxes, added items, removals, renames, and reordering carry over to units that still exist (including units that were merged or split). If the new term drops a unit you edited, the builder asks before discarding those edits.
