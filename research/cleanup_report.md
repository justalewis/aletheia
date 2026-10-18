# Catalog cleanup report, 2026-10-17

This pass checked every catalog link, settled the license conflicts flagged in the first research pass, and rechecked unverified licenses. All fixes live in `research/overrides.json` or in `tools/build_catalog.py`. The generated catalog was not hand-edited, and `python -I tools\build_catalog.py` re-runs cleanly. No items were dropped. Nine distinct items that the old merge had collapsed into other records are now restored (see "Merge script fixes").

**Catalog now:** 2,188 readings (was 2,179), 623 assignments, 47 sources.

## 1. Links

`tools/link_check.py` requested every `landing_url`, `html_url`, `pdf_url` and `epub_url` (readings) and every `url` and `file_url` (assignments). It made one request at a time per host, waited 2 s between requests to the same host (3 s for wacclearinghouse.org), and sent a browser User-Agent that names the tool. Results, keyed by URL with the record ids that use each one, are in `research/link_check.json`.

| Status | URLs | What it means |
|---|---:|---|
| live | 3,120 | 2xx, directly or after an http→https / www / trailing-slash redirect |
| redirected | 52 | 2xx after moving to a different address (`final_url` records where) |
| bot-gated | 672 | Challenge page; not pursued |
| blocked | 4 | 403 to automated requests, no challenge page (cdc.gov ×3, repository.arizona.edu) |
| dead | 2 | 404 |
| error | 1 | Connection closed (openresearch-repository.anu.edu.au), possibly temporary |
| **total** | **3,851** | |

- **Bot-gated:** 474 are wacclearinghouse.org `/docs/` files. These were marked by rule and never requested. The other 198 returned a challenge: BCcampus Pressbooks (55), ACRL Sandbox file downloads (28, which redirect to `/challenge`), CUNY Academic Works (19), opentextbc.ca (18), Composition Studies (11), and smaller hosts. A person in a browser can probably open them. Note that 401 of the 728 `pdf_url` links are bot-gated, so the exporter can't fetch most PDFs automatically. 102 records (88 readings, 14 assignments) have no link outside a bot check.
- **Dead:** `writingcommons.org/section/composing-with-ai/` (record `writing-commons-composing-with-ai-section`) and `skillscommons.org/handle/taaccct/13764` (`oercommons-lbcc-wr227-technical-writing`, file_url).
- **Redirects worth a look:** Three Lumen Learning courses now redirect to quillbot.com (`basic-reading-and-writing-lumen`, `english-comp-i-rhetorical-methods-lumen`, `english-comp-ii-rhetorical-methods-lumen`). The license on the new host hasn't been checked. The *Free Culture* PDF link now lands on a lessig.org product page (`lessig-free-culture`). Other moves are host migrations with the same content: Informed Arguments to odp.library.tamu.edu, NSCC Communication at Work to atlanticoer, Tulsa CC Comp to openlearnok, Penn State Style for Students to courses.ems.psu.edu. URLs in the catalog were left as they were; `final_url` has each new address.
- **"PDF" links that serve HTML:** 6 Wiki Education `pdf_url`/`file_url` values point at Commons file *description* pages, not at the files on upload.wikimedia.org. The Saylor *Exploring Perspectives* PDF and the *Free Culture* PDF also return HTML.
- **Certificate warnings (502 URLs)** come from this machine's Python trust store, not from the sites. Windows' own TLS stack accepts en.wikisource.org while Python reports the certificate as "expired." The checker retries such URLs without verification and records a `warning`.

Re-running `python -I tools\link_check.py` checks only URLs not already in the file. `--recheck` checks everything again, and `--retry STATUS` re-checks one status.

## 2. License conflicts

Rule applied: when sources disagree, `license` holds the most restrictive license and the other statement goes in `notes`. Each note starts "License check 2026-10-17:" and names the evidence.

| Item | Records | Was → now | Evidence |
|---|---:|---|---|
| Writing Spaces Vols. 1–2 (chapters whose footnote says NC-SA) | 33 | CC BY-NC-SA 3.0 US → **CC BY-NC-ND 3.0 US** | PDF volume page: NC-ND 3.0 US "unless otherwise stated"; WAC page (rechecked): NC-ND 4.0. The 7 chapters already NC-ND and the two volume records were unchanged. |
| Bad Ideas About Writing (book + all chapters) | 64 | CC BY 4.0 → **CC BY-NC-ND 4.0** | WVU book page (rechecked) states NC-ND 4.0 and is specific to this book; PDF, OTL and LibreTexts say CC BY 4.0 |
| OpenStax *Writing Guide with Handbook* | 22 | already CC BY-NC-SA 4.0 | OpenStax CMS API and book pages confirm NC-SA 4.0; OTL's "CC BY" is the outdated one. No change. |
| *How Arguments Work* (book + chapters) | 17 | CC BY-NC 4.0 → **CC BY-NC-SA** | OTL lists NC-SA (rechecked); LibreTexts says BY-NC 4.0 |
| Open Technical Communication 1e / 2e | 38 | no change | Different editions, not a conflict. OpenALG metadata (rechecked) gives 1e CC BY 4.0 and 2e CC BY-NC-SA 4.0, and every chapter record points at its own edition. |
| Atkinson & Corbitt: *Handbook for Mindful Technical Writing*, *Mindful Technical Writing* | 2 | CC BY-SA 4.0 → **CC BY-NC-SA** | Montana Tech repository (rechecked): BY-SA 4.0; OTL (first pass): NC-SA |
| Atkinson & Corbitt: *Advanced-Level Writing* | 1 | now verified CC BY-NC-SA | OTL page (rechecked) |
| Penrose & Katz, *Writing in the Sciences* | 1 | CC BY-NC-SA 4.0 → **CC BY-NC-ND 4.0** | Parlor product page (rechecked): NC-SA 4.0; Parlor OA collection listing (first pass, now 404): NC-ND 4.0 |

Other conflicts found along the way and handled the same way:

- *The Archive as Classroom* (CCDP) and its one chapter record: CC BY 4.0 → CC BY-NC-ND 4.0. The CCDP catalog page says NC-ND 4.0. The book site's footer pairs "(cc) BY 4.0" with an "All rights reserved" line from its web template.
- *The Muse* (MTSU): CC BY-NC 4.0 → CC BY-NC-SA (OTL).
- Gagich & Zickel, *Guide to Rhetoric, Genre, and Success* 2e (book record): CC BY-NC 4.0 → CC BY-NC-SA 4.0. It adapts an NC-SA original, and ShareAlike carries over. Chapter records keep their page-level licenses.
- *Rhetoric and Composition* (Wikibooks/LibreTexts): CC BY-SA → CC BY-NC-SA 4.0. The record's `html_url` is the LibreTexts copy, which is tagged NC-SA.
- Whole works stricter than their parts. The merge now does this automatically: a book, collection or journal takes its strictest part's license and keeps its own statement in notes. This affected *88 Open Essays* (CC BY 4.0 → CC BY-NC-ND 4.0), *A Dam Good Argument* (CC BY 4.0 → CC BY-NC-ND 4.0), *Write What Matters* (CC BY-NC-SA 4.0 → CC BY-NC-ND 4.0) and *Writers: Craft & Context* (CC BY-NC → CC BY-NC-SA 4.0). Chapter-level reuse should use the chapter records.

**Totals:** license strings changed on 140 records (136 readings, 4 assignments). Reuse-tier moves among readings: adapt → verbatim 68, adapt-nc → verbatim 35, adapt → adapt-nc 3. Unknown/link-only items newly licensed: 5 readings. No record moved to a looser tier except those 5, which went from unknown to a verified license. Readings by tier are now adapt 541, adapt-nc 804, verbatim 674, link-only 169.

**Update after review (2026-10-17):** Justin says he cleared every CC-licensed reading beforehand, so all of them can be used. None of the 1,878 CC readings or 567 CC assignments is link-only now; each one can already go into a reader. The `license` fields still record what the sources say, because attributions depend on them and other instructors won't share Justin's clearance. Nobody needs to ask WVU or Writing Spaces now. Justin confirmed the clearance covers using the CC readings as published, not editing them, so the reuse tiers stay as they are: ND works go in whole and unmodified, and NC terms still apply.

**Judgment calls (written before that clearance; still matter for other instructors):**

- *Writing Spaces 1–2.* Writing Spaces' Terms of Use say the license on a chapter's first page governs. If that footnote is accepted as controlling, the 33 chapters can go back to CC BY-NC-SA 3.0 US (adapt-nc). The rule as written forced NC-ND.
- *Bad Ideas.* Only WVU says NC-ND, and WVU is the publisher. One email to WVU's Digital Publishing Institute would settle it.
- *Public-domain texts on hosts with their own footers.* Wikisource shows a CC BY-SA site footer, Perseus licenses its digital editions CC BY-SA 3.0 US, and Internet Archive and Avalon carry no license. These were kept as Public Domain, because a faithful transcription adds no new copyright in the US. Isocrates records note Perseus's terms. The Augustine record stays link-only because its HTML link is New Advent's revised, copyrighted text.
- *CCDP template footer.* The "All rights reserved" line was treated as web-template text, not as a license statement.

## 3. Unverified licenses

| | Unverified before | After | Empty license before | After |
|---|---:|---:|---:|---:|
| Readings | 74 | 9 | 12 | 7 |
| Assignments | 26 | 4 | 7 | 4 |

Verified this pass:

- **By page fetch:** 4 LiCS articles (CC BY-NC-ND 4.0); 11 Writing Commons pages (all rights reserved; link only); JUMP+ (7), Stylus (6) and Xchanges (6), which show no open license on article, About or Submissions pages (link only); 88 Open Essays #75 (CC BY-ND 4.0) and #52 (Public Domain Mark); both TextGenEd collections (CC BY-NC 4.0, from item pages); 14 Composition Studies and 4 Kairos PraxisWiki assignments (no license; link only); 3 MERLOT assignments marked "Creative Commons: No" (link only).
- **By metadata:** *Digital Samaritans* (DRC), CC BY-NC-ND 4.0 via Crossref; Wiki Ed *Editing Wikipedia* brochure, CC BY-SA 4.0 via the Commons API.
- **By rule (26 primary texts):** 11 are U.S. federal works (17 U.S.C. 105). 14 were published before 1931. The Augustine record is noted above.

Still flagged (all link-only except one):

- `diving-into-rhetoric-palmer`: pressbooks.pub serves a Cloudflare check. Its CC BY-NC-SA comes from a search summary, so this is the one unverified record that still allows adaptation (adapt-nc).
- DRC books `drc-100-years-new-media-pedagogy`, `drc-making-space`, `drc-on-visual-rhetoric` and `drc-rhizcomics`: the DRC site is bot-gated, and Crossref has no license for them.
- `writing-spaces-essay-clusters`, `writing-spaces-essay-sequences`, `writing-spaces-vol-7`: index pages with no license of their own (Vol. 7 is in progress).
- `writing-commons-composing-with-ai-section`: the page returns 404.
- Assignments `kcworks-project-inspire-science-communication-taylor` (no rights field), `merlot-downs-quote-sandwich` and `merlot-hanlon-writing-with-a-fable` (MERLOT says "Unknown"), and `wsaaa-using-recipe-archives-place-based-research-writing` (no license; the download points to another entry's PDF).

Suggestion for the exporter: treat `license_verified: false` as link-only until someone checks it.

## 4. Merge script fixes (`tools/build_catalog.py`)

- **Distinct items were being merged away.** URL matching ignored query strings, and title matching joined same-titled records from one source file. Nine items are restored: `college-esl-writers`, `exploring-public-speaking-4e`, `processes-writing-across-academic-careers`, `teaching-autoethnography-tombro`, `e88-65-ending-the-secrecy-of-the-student-debt-crisis`, `plos-tsr-2013-literature-review`, `plos-tsr-2018-popular-science-book`, `plos-tsr-2026-writing-peer-review` and `wac-methods-digital-writing-research-v2`. These rows are not yet in the Google Sheet.
- **License conflicts inside merged duplicates** now resolve to the most restrictive license, with the others noted.
- **Chapters pointing at a merged-away book id** now point at the surviving record. The 63 Bad Ideas chapters, for example, link to `bad-ideas-about-writing`. No dangling `parent_id` values remain.
- **New:** whole works are tightened to their strictest part (see above); `research/overrides.json` is applied after the merge; and `research/catalog/merge_log.txt` lists every override and tightening (228 entries this run). `stats.md` gained two lines counting them.

## Files

- `tools/link_check.py` (new), `research/link_check.json` (new)
- `research/overrides.json` (new): 13 rules and 32 per-record entries, each with its evidence
- `tools/build_catalog.py` (changed as above); `research/catalog/*` regenerated, including the Sheet TSV chunks
- Nothing was uploaded to Drive.
