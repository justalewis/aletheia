# Harvesters

Scripts that find new openly licensed writing books on Pressbooks and in the Open Textbook Library, and put them in a review queue. Nothing reaches the pack (`corpus/rhetcomp/readings.jsonl`) until someone approves it.

Run everything from the repository root with the backend virtualenv's Python (`backend\.venv\Scripts\python.exe` on Windows). The scripts need only `requests`, which is already in `backend/requirements.txt`.

| File | What it does |
|---|---|
| `common.py` | Polite HTTP, the subject filter, skin/topic tagging, mapping to the pack schema |
| `pressbooks.py` | Pressbooks Directory feed and network lists, then each book's metadata and table of contents |
| `otl.py` | Open Textbook Library API, slowly, with a resumable state file |
| `merge.py` | Dedupe, write the review queue, approve records into the pack |
| `test_harvest.py` | Tests for the pure parts (`python -m pytest tools/harvest -q`) |

## 1. Pressbooks

```
python tools/harvest/pressbooks.py --max-books 300 --network https://opentextbc.ca --network https://pressbooks.bccampus.ca
```

- Reads the Pressbooks Directory OPDS feed (about 176 pages of 20), plus the book list of each `--network`.
- Screens every book on its title, Directory subjects, and short description, then calls `/wp-json/pressbooks/v2/metadata` and `/toc` for up to `--max-books` books that pass. The full filter runs again with the book's Thema/BISAC codes and full description.
- Emits a book record and one record per chapter. Front matter, back matter, and part pages are left out unless they run 1,000+ words and aren't boilerplate (acknowledgements, glossary, licensing, and so on). Chapters under 100 words are skipped. Each chapter carries its own license from its metadata, falling back to the book's; a book whose chapters are stricter than it takes the strictest chapter license, as `build_catalog.tighten_parents` does.
- Most Pressbooks-hosted sites (`*.pressbooks.pub` and many custom domains on the same service) answer the harvester with a CloudFront 403. Those hosts are skipped after one request, never retried, and once two hosts under one domain refuse, the rest of that domain is skipped without asking. A book on a refused host that clearly passes the filter still gets a book-level record from the Directory data, with no license, so it stays link-only until someone checks.
- Responses are cached in `research/harvest/raw/pressbooks/` (gitignored), so a rerun is cheap. `--refresh` ignores the cache, and `--retry-blocked` forgets refused hosts.
- Writes `research/harvest/pressbooks-candidates.jsonl`, `pressbooks-summary.json`, and `pressbooks-hosts.json` (every Directory host, which `otl.py` uses to spot Pressbooks books).

## 2. Open Textbook Library

```
python tools/harvest/otl.py --max-requests 12
python tools/harvest/otl.py --max-requests 12     # again, later: it resumes
```

- `open.umn.edu` sets `Crawl-delay: 120` in robots.txt, so the script makes one request every two minutes. Twelve requests take about 24 minutes.
- All progress lives in `research/harvest/otl-state.json` (gitignored): the subject list, the query plan, pages done and still queued, every book collected, and the time of the last request, so a restarted run waits out the delay too. Ctrl+C is safe.
- The plan pages through "Literature, Rhetoric, and Poetry", "Journalism, Media Studies & Communications", "Library Science and Museum Studies", and "Student Success" in full, then runs keyword searches ("writing", "communication") inside Business, Engineering, Languages, Education, and the sciences. Edit `FULL_SUBJECTS` and `SCOPED_SEARCHES` in `otl.py`, then run with `--replan`.
- Library subjects are whole fields, so they don't count toward a book's filter score; title and description decide.
- Records are book level (the API has no table of contents), with license, description, and PDF/eBook/online links. The Library's license names have no version, and the license is the Library's report, not the book's own statement, so `license_verified` is false (link-only until someone checks).
- A book whose online version is on a Pressbooks site gets `harvest.pressbooks_url`. Then `python tools/harvest/pressbooks.py --from-otl` fetches those books' chapters (it reuses the cache, so rerunning it costs only the new books).
- `--write-only` rewrites `otl-candidates.jsonl` and `otl-summary.json` from the state without any requests.

## 3. Review queue

```
python tools/harvest/merge.py
```

Writes `research/harvest/queue-YYYYMMDD.jsonl` and `queue-YYYYMMDD.md`.

- The same book from Pressbooks and the Library becomes one record (the Pressbooks one, which has chapters and the book's own license). If the sources disagree about the license, the most restrictive is recorded and the note says so.
- Exact duplicates of pack records (same normalized URL, or same normalized title and parent) are left out of the queue and listed at the end of the .md file. New chapters of a book already in the pack stay in the queue, pointing at the existing book's id.
- Near duplicates (same title once numbering, subtitles, and edition words are stripped, such as a new edition) stay in the queue with `queue.status: "near-duplicate"` and `queue.match` naming the pack records.
- Every queued record has a proposed permanent id that collides with nothing in the pack, `needs_review: true`, and a `harvest` block (source, keys, filter score and reasons) that is dropped on approval.

### What the review step expects

Skim the .md file. For each book you want:

1. Check the license on the book's own page, especially for records marked *license not fetched* or *license unverified*. Fix `license`, `license_url`, and `license_verified` on the queue line if needed (`license_family` and `reuse_tier` are recomputed on approval).
2. Check the skins, topics, `course_level`, and `audience`. They are keyword guesses.
3. For a near duplicate, decide whether it replaces nothing (approve it by id) or is the same work (leave it).

Then approve:

```
python tools/harvest/merge.py --approve all --queue research/harvest/queue-20261017.jsonl --dry-run
python tools/harvest/merge.py --approve book-id,other-book-id --queue research/harvest/queue-20261017.jsonl
cd backend
python manage.py load_pack rhetcomp
```

- `all` approves every record marked new; near duplicates must be named. New chapters of a near-duplicate book wait until the book is named.
- A book id brings its chapters (`--book-only` skips them); a chapter id brings its book if the book isn't in the pack.
- Approved records are appended to `readings.jsonl` with their ids (renamed with a suffix only if the id was taken since the queue was written). Existing lines are never touched, the file stays sorted by id as `tools/export_pack.py` writes it, and approved queue lines are marked `approved` so a second run doesn't add them again.
- Approved records keep `needs_review: true` in the pack, which `load_pack` ignores, so a later pass can find the records whose tags haven't been checked by hand.
