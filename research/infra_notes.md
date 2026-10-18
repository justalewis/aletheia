# OA Textbook Builder: infrastructure and landscape notes

Prepared by the infra research agent, 2026-10-17. Prices, versions, and endpoint behavior were checked on that date against vendor docs, source code, or live endpoints; bracketed numbers point to the source list at the end. "Unverified" means no primary source confirmed the claim. Per-repository detail lives in `raw/repositories_sources.json` (25 records).

## Recommendations at a glance

1. **Harvest first:** Open Textbook Library API, Pressbooks network/book APIs (with the Pressbooks Directory OPDS feed for discovery), LibreTexts Commons API, OpenStax CMS API, OAPEN daily JSON plus DOAB OAI-PMH, DOAJ API/OAI, OpenAlex (free key), Project Gutenberg catalog feeds, Wikisource API, KC Works API, and WAC Clearinghouse sitemap/REST with manual license capture.
2. **Treat as link-out or manual:** OER Commons (token required, scraping barred by ToS), MERLOT (fee-based key), OASIS (no API), Open Syllabus (proprietary), Internet Archive Scholar (API unreachable). Unpaywall is now a legacy layer over OpenAlex; skip it.
3. **LMS export:** generate one Common Cartridge **1.1** package that also carries Canvas's `course_settings/` extensions, plus a CC 1.1 weblinks-only fallback. Test-import in each LMS before claiming support.
4. **Reader export:** one HTML + CSS paged-media template rendered with **WeasyPrint** (tagged PDF/UA), validated with an external checker; EPUB via Pandoc, gated on EPUBCheck and Ace. DocRaptor (hosted Prince) as paid fallback.
5. **Licensing model:** a verbatim reader is a CC *collection*, so mixed licenses can coexist. Store license and version per item, generate TASL attributions, lock edits on ND items, and warn on NC items when paid printing is involved. Two core writing sources need this now: Writing Spaces (CC BY-NC-ND) and OpenStax *Writing Guide with Handbook* (now CC BY-NC-SA 4.0, not CC BY).
6. **Hosting:** Render or Railway (about $15 to $25/month) for least ops; Fly.io plus Neon for lowest cost with per-job workers. Keep PDFs in Cloudflare R2 or Tigris (free egress).
7. **AI layer:** Postgres + pgvector, hybrid (vector + full-text) retrieval, and an LLM that sequences only catalog IDs it was handed. Strip student names from uploads before any external API call.

## 1. Harvestable sources (summary)

| Tier | Source | Best method | Auth | License in metadata? | Notes |
|---|---|---|---|---|---|
| 1 | Open Textbook Library | JSON API, 2,015 books [1] | none | yes (name string) | Reviews and LC subjects; no TOC; format links point to hosts. Crawl-delay 120. |
| 1 | Pressbooks networks | `/wp-json/pressbooks/v2/` [2] | none (public) | yes, book and chapter | Full chapter HTML and TOC; exports only if author shares them; Cloudflare on pressbooks.pub. |
| 1 | Pressbooks Directory | OPDS 2.0 JSON, 3,509 items [3] | none | no (in UI facet only) | Discovery list; pull detail from each book's API. |
| 1 | LibreTexts | Commons API, 4,102 books [4] | none (Commons) | yes | Library-page API needs a token; `/licensereport` route per book. |
| 1 | OpenStax | Wagtail CMS API, 129 books [5] | none | yes, with version | 80 of 129 titles are CC BY-NC-SA; Writing Guide is BY-NC-SA 4.0 [6]. |
| 1 | OAPEN / DOAB | daily JSON dump; OAI-PMH [7][8] | none | yes (CC URI) | Thema subjects; REST search returned 403. Dedupe DOAB against OAPEN. |
| 1 | DOAJ | API v4 + OAI-PMH [9] | none | journal level only | 2 req/s; 1,000-record paging cap; free feeds lag up to 30 days. |
| 1 | OpenAlex | API + free S3 snapshot [10] | free key | yes, per location | Usage-priced: $1/day free with key. Topic T11982 "Rhetoric and Communication Studies." |
| 1 | Gutenberg | catalog feeds, rsync mirrors [13] | none | US public domain | Main-site crawling is blocked by policy; Gutendex for JSON. |
| 1 | Wikisource | MediaWiki API, WS Export [14] | User-Agent | page templates | Per-minute limits by identity; dumps preferred for bulk. |
| 1 | KC Works | InvenioRDM API, OAI-PMH [15] | none | yes (`rights.id`) | Mixed CC and all-rights-reserved; strip `submitter_email`. |
| 2 | WAC Clearinghouse | Yoast sitemaps + WP REST [16] | none | **no** (in page HTML/PDF) | Site moved to wacclearinghouse.org; many titles NC-ND; PDFs 403 to scripts. |
| 2 | Writing Spaces | manual (per-essay PDFs) | none | no | CC BY-NC-ND (3.0 US / 4.0 by volume), subject to its Terms of Use (unread: site returned 500). |
| 2 | Crossref | REST [11] | polite pool | yes (license[]) | Enrichment; count CC only if `vor` and zero delay. |
| 2 | CORE | API v3 [12] | free key advised | partial | 1,000 tokens/day personal tier. |
| 2 | Wiki Education | undocumented JSON + repo YAML [20] | none | CC BY-SA 4.0 | Training modules on sources, citation, plagiarism. |
| 3 | OER Commons | token API [17] | token by request | yes | ToS prohibits scraping. |
| 3 | MERLOT | web services [18] | fee + key | varies | Referatory; low priority. |
| 3 | OASIS | none (HTML search) [21] | none | facet only | 132 sources; use its source list as a checklist. |
| 3 | Open Syllabus | none [19] | subscription | n/a | Manual lookup of most-assigned titles only; do not scrape. |
| 3 | IA Scholar | none (fatcat API down) | n/a | n/a | Bulk exports last updated 2024. |

**Design implications.** TOC and chapter text come from hosts (Pressbooks, LibreTexts, OpenStax GitHub, OAPEN files), and license data ranges from structured (Pressbooks, OpenAlex, KC Works) to free text (WAC, Writing Spaces). Build one harvester per *platform* (Pressbooks, OAI-PMH, InvenioRDM, WordPress REST, MediaWiki), not per site. One Pressbooks adapter then covers BCcampus, eCampusOntario, UMN, and Rebus Press, and retargeting to another discipline becomes new filters and subject crosswalks (Thema, LCC, LCSH, FAST, OpenAlex topics) rather than new code. OpenAlex responses include a notice asking AI agents to request keys on the user's behalf; nobody acted on it, and the project owner should create the key directly.

## 2. LMS export

**Common Cartridge versions.** CC 1.1 (2011) added LTI links and `.imscc` [23]. CC 1.2 added standards metadata and `intendeduse="assignment"`. CC 1.3 (2013) made resource types extensible and allows assignments only through an extension, which Canvas emits; its QTI profile is QTI 1.2.1 [24]. Thin CC allows only web links and LTI links [25]. CC 1.4 is "Candidate Final" and no major LMS detects it [26].

| LMS | Imports | HTML pages | Weblinks | Discussions | Assignments | QTI | Structure |
|---|---|---|---|---|---|---|---|
| Canvas (generic CC) [27][28] | 1.0–1.3, Thin | **as Files** unless a hidden setting is on | yes | yes | yes | yes | folders → modules |
| Canvas (Canvas-flavored) | 1.1/1.3 + `course_settings/` | Pages | yes | yes | yes | yes | `module_meta.xml` |
| Moodle 4.x/5.x [29] | **1.0, 1.1 only**, strict XSD | Page | URL | Forum | **no** | partial | top level only; deeper → labels |
| Blackboard Original [30] | 1.0–1.2 | yes | yes | partial | partial | partial | yes |
| Blackboard Ultra [30][32] | generic CC undocumented; Canvas packages use a Canvas importer | via importer | ? | via importer | via importer | via importer | modules → folders |
| D2L Brightspace [33] | 1.0–1.3, Thin 1.3 | yes | ? | ? | not via intendeduse | yes | ? |
| Sakai Lessons [34] | 1.0–1.3 | links; Canvas `wiki_content` → pages | yes | Forums | yes | yes | pages/subpages |
| Schoology [35] | has CC import; details unverified | ? | ? | ? | ? | ? | ? |
| Google Classroom [36] | no CC import; API for topics, coursework, link materials | no | API | no | API | no | API topics |

Blackboard Original cannot be used for teaching after December 31, 2026; Ultra is mandatory from January 1, 2027 [31]. Target Ultra.

**Canvas extensions.** Canvas treats a package as its own only when the manifest registers `course_settings/canvas_export.txt`; it then runs its native importer [27]. Emit `course_settings.xml`, `module_meta.xml` (module items typed WikiPage, Assignment, DiscussionTopic, ExternalUrl, ContextModuleSubHeader, with `indent` and `new_tab`) [37], wiki pages at `wiki_content/<slug>.html` with `<meta name="identifier">`, and `assignment_settings.xml`. Internal link tokens: `$IMS-CC-FILEBASE$/path`, `$WIKI_REFERENCE$/pages/<id>`, `$CANVAS_OBJECT_REFERENCE$/assignments/<id>`. Canvas's own exporter writes `schemaversion 1.1.0` with both the standard layer and the Canvas layer, which is why other LMSs can still read its exports.

**Moodle .mbz** is undocumented beyond plugin APIs, changes across versions, and has no maintained generator [39]. Skip it.

**Generator libraries.** No mature, broadly maintained IMSCC library exists for Python or Node. Best references: Tsugi's `CC.php` (PHP; writes CC 1.1 with Canvas `module_meta.xml`) [40], Pressbooks' weblinks exporter [41], and canvas-lms `lib/cc/` [27]. Newer packages exist (@courseforge/imscc, TypeScript, Canvas-only [42]; canvas-course-kit, Python, created Aug 2026 [43]); text2qti (Python) generates QTI 1.2 for Canvas [44]; the canvas_cc gem is stale (2019) [45]. A small in-house generator (manifest plus a few XML templates) is the lower-risk path.

**LTI 1.3 Deep Linking (phase 2).** Deep Linking 2.0 returns links, files, or HTML items to the LMS [46]. Canvas's `module_index_menu_modal` placement builds a whole module from returned items, so an instructor could pick readings inside Canvas [47]. Cost: per-institution developer-key registration, HTTPS, JWKS key rotation. Libraries: ltijs (TypeScript, active) [48], packbackbooks lti-1-3-php-library (active) [49]; pylti1.3 last released Nov 2022 [50].

**Gotchas.**
- Generic-CC HTML lands in Canvas as Files, and Canvas silently drops module items whose identifierref doesn't resolve. Ship `wiki_content` pages and validate every reference.
- Canvas's sanitizer strips `script`, `style`, `form`; iframes need http(s) `src`; inline styles are allowlisted [38]. Keep reader HTML plain and prefer absolute links to canonical reading URLs over Canvas-only tokens.
- Use `$IMS-CC-FILEBASE$`. The 1EdTech-hosted 1.3 spec now prints `$1EdTech-CC-FILEBASE$`, which Canvas won't match.
- Keep identifiers stable and unique (Canvas overwrites on match, D2L duplicates), hierarchy at two levels (Moodle flattens deeper folders), and packages under 100 MB.
- Don't emit 1.3/Thin (Moodle can't read them) or 1.4 (Canvas can't detect it). Thin CC imports into Canvas as one module unless toggled [51].

**Recommendation.** First target: one CC 1.1 package with the standard layer (HTML webcontent, `imswl` weblinks, `imsdt` discussion prompts) plus the Canvas layer. Canvas gets real Pages and modules, Blackboard SaaS runs its Canvas importer, Sakai reads Canvas wiki pages and assignments, and Moodle and D2L read the 1.1 layer. Whether Moodle and D2L tolerate the extra Canvas files is untested. Fallback: a CC 1.1 weblinks-only "link pack" (the Pressbooks and LibreTexts approach). Later: QTI 1.2.1 quizzes, LTI Deep Linking, Google Classroom API.

## 3. Reader export (PDF, HTML, EPUB) and mixed licenses

| Tool | Cost | Paged media | Accessibility | Status |
|---|---|---|---|---|
| WeasyPrint | free, BSD | margin boxes, running heads, target-counter TOC, footnotes, bookmarks | `--pdf-tags`; PDF/UA-1 and UA-2; "not guaranteed to be valid" [53] | v70.0, Sept 2026 [52]; no browser |
| Prince | server $3,800 noncommercial, $1,900 academic; free license watermarks [54][83] | best in class | PDF/UA-1, PDF/A+UA [55] | v17, Sept 2026; Pressbooks' engine [58] |
| DocRaptor | free 5 docs/mo; $15/mo for 125 [56] | Prince 15.1 | PDF/UA-1 profile [57] | active |
| Paged.js | free, MIT | good, in Chromium | tagging is a "long-term goal" [59] | last stable npm 0.4.3 (2023) [60] |
| Vivliostyle | free, **AGPL** [61] | strong | Chromium tagging; PDF/UA unverified | v11.3.3, Sept 2026 |
| Typst | free, Apache-2.0 | own markup, not CSS | tagged by default since 0.14; opt-in PDF/UA-1 [62][63] | v0.15.1; HTML export experimental |
| Pandoc + LuaLaTeX | free | excellent typography | `pdfstandard=ua-2` needs TeX Live 2025; tagging still in development [64][65] | large install |
| Chromium print | free | basic `@page` only | Puppeteer `tagged` default on (experimental); Playwright off [66][67] | fine for handouts |

**Recommendation.** WeasyPrint as the primary engine (run as a Python sidecar if the app is Node), with every build checked by an external PDF/UA validator such as PAC [57]. DocRaptor as fallback using the same HTML. Avoid Paged.js as the only path, and get a legal read before hosting AGPL Vivliostyle. EPUB: Pandoc, then EPUBCheck 5.4.0 [68] and Ace by DAISY [69]. A clean EPUB also feeds Manifold, Perusall, and Pressbooks import.

**Collection vs. adaptation.** CC 4.0 defines Adapted Material as material "translated, altered, arranged, transformed, or otherwise modified in a manner requiring permission" [70]; the 3.0 code said a Collection "will not be considered an Adaptation" [72]. The CC FAQ makes adaptation the threshold question: unadapted works can be combined if each is attributed and NC is respected, and ND works may be reproduced unmodified "together with other material in a collection" [75]. A verbatim, side-by-side reader is a collection; each item keeps its license.

**Adaptation triggers** (editing within a chapter, translating, merging sources) bring compatibility rules: BY-SA output must stay BY-SA or compatible, NC stays NC, ND cannot be shared adapted, and SA + NC-SA is incompatible [71][75][76].

**ND items.** Format shifting "never produces Adapted Material," so re-typesetting is fine [71]. BY-ND 4.0 permits sharing "in whole or in part" [71]; excerpting a 3.0 ND work is murkier because the 3.0 Collection definition requires the work "in its entirety" [73].

**Attribution.** TASL (Title, Author, Source, License) per item, linked to the deed, full URLs in print [77][78]. 4.0 also requires keeping supplied notices and marking modifications [70]; Title is required through 3.0 [79]. The reader's own license covers only selection, arrangement, and new material [75]. Public Domain Mark items need no attribution [80].

**NC and printing.** NC means "not primarily intended for or directed towards commercial advantage or monetary compensation" [74]. *Great Minds v. FedEx* (2d Cir.) and *v. Office Depot* (9th Cir.) let a noncommercial licensee hire a for-profit copy shop [81][82]. Free readers and instructor- or campus-ordered prints are fine; a print-on-demand storefront selling NC readers for profit is high risk. Cost-recovery pricing guidance is unverified (secondary sources only).

**Software rules.**
- Store license code, version, deed URL, title, author, source URL, copyright notice, and a modified flag per item.
- Auto-generate per-chapter TASL blocks and an attribution appendix.
- Lock text edits on ND items, and require a "changes made" note before saving edits to other items.
- Block ND content and SA + NC-SA pairs in merged chapters.
- Warn when a reader containing NC items goes to paid print.

## 4. Prior art and comparable tools

| Tool | What it does | Gap / integration |
|---|---|---|
| Pressbooks / Pressbooks EDU | Authoring and publishing. Cloning copies public books with derivative-permitting licenses (not ND) [84]. Exports EPUB, PDF, and Common Cartridge with LTI or web links; parts become Canvas modules [85]. Imports EPUB, DOCX, ODT, WXR, HTML; not PDF [86]. | Book-centric, single-platform. **Integrate**: harvest via API; later "push to Pressbooks" by emitting EPUB or WXR. |
| LibreTexts | Libraries plus Composer/Remixer (drag-and-drop, paragraph-level attribution), Conductor, Commons, ADAPT [87]. Remixes transclude source pages [88]. | Remix only inside LibreTexts; large remixes can fail PDF builds [88]. **Import** from Commons; don't rebuild a remixer. |
| OER Commons | Discovery, Open Author modules, Groups, Hubs [89]. | No compilation or export. Link out. |
| OpenStax | Fixed peer-reviewed books; Canvas Commons cartridge of section links [90]; OpenStax Assignable began a per-student fee in Fall 2025 [91]. | Link to sections; Writing Guide is BY-NC-SA, so mind NC in print. |
| Rebus Foundation | Now training and consulting programs; Rebus Press is a Pressbooks network [92]. | Content source, not a tool. |
| Manifold | Open-source publishing with annotation and reading groups; ingests EPUB, HTML, Markdown, Word, Google Docs [93]. | **Integration target** for our EPUB. |
| Mason OER Metafinder | Real-time federated search over about 22 sources; GMU warns not all results are strictly open [94]. | Discovery only, no rights filtering. |
| OASIS | 132-source OER search [21]. | Discovery only. |
| Open Course Library | 81 WA community-college courses, CC BY, 2011–2013 [95]. | Dated model of course packages. |
| Lumen Learning | Waymaker, OHM, Candela (Pressbooks-based) courseware on OER, sold per student [96]. | Proprietary layer. |
| Top Hat; Perusall | Commercial. Top Hat textbooks are importable and editable in Top Hat [97]; Perusall takes uploaded PDF/EPUB/web pages, and catalog OER may carry a student fee [98]. | Our PDF/EPUB should upload cleanly. |
| Open Syllabus Galaxy | Co-assignment map of most-assigned titles [99]. | Signal for sample arcs (manual use only, given terms). |
| Kudu (UCLA spinout) | Low-cost interactive textbook platform; used for an AI-generated humanities textbook in 2025 [100]. | Not OER. |
| Leganto (Ex Libris) | Library reading-list system with LMS integration and OER/OA filters [101]. | Lists links, no compiled reader or cartridge. Closest library-side analog. |

**Gap we fill.** No existing tool combines discipline skins for writing courses, term-length-aware course arcs, license-aware compilation across repositories with generated attribution, and one-step reader plus LMS skeleton export.

## 5. Hosting

Assumed load: web app, one worker for harvesting and PDF rendering (about 1 GB RAM), Postgres with pgvector, 3 to 5 GB of PDFs.

| Option | Key prices | Rough monthly | Background jobs |
|---|---|---|---|
| Fly.io | shared-cpu 512 MB $3.69, 1 GB $6.70, billed per second running; volumes $0.15/GB; egress $0.02/GB; no free tier for new orgs [102]. Managed Postgres from $38 + $0.28/GB, pgvector supported [102][103]; self-run Fly Postgres "unsupported" (~$2). Tigris: 5 GB free, $0.02/GB, free egress [105]. Autostop scales web Machines to zero; Machines without services don't autostart [104]. | $8–15 (Neon or unmanaged DB); $45–55 (Managed Postgres) | Good: worker process group or per-job Machines. |
| Render | Starter $7 (web or worker); Postgres basic-256mb $6; disk $0.25/GB; Hobby includes 5 GB egress, then $0.15/GB [106][107]. Free web sleeps after 15 min; free Postgres expires at 30 days [108]. pgvector on PG 13+ [109]. | $20–25 | Easy: workers and cron ($1 min) built in. |
| Railway | Hobby $5 incl. $5 usage; ≈$20/vCPU-mo, $10/GB RAM-mo; volumes $0.15/GB; egress $0.05/GB; buckets $0.015/GB [110]. Cron, 5-min minimum [111]. | $10–20 | Easy. |
| Vercel + Supabase | Vercel Hobby is "personal, non-commercial"; Pro $20 [112]. Functions max 300 s Hobby, 800 s Pro; 4.5 MB body limit [113]. Supabase Free: 500 MB DB, pauses after a week idle; Pro $25 [114]; pgvector available [115]. | $0 prototype; ~$45 real | Poor fit for long PDF renders and crawls. |
| Cloudflare | Workers Paid $5 minimum; D1, Queues, Cron Triggers, Containers [117]. R2: 10 GB free, $0.015/GB, zero egress [118]. Browser Rendering `/pdf` endpoint (HTML to PDF), 10 browser-hours included, then $0.09/hr [119][120]. Vectorize for embeddings [121]. | $5–10 | Workable, but forces a Workers-native design (no Postgres without an external DB). |
| Neon (DB only) | Free: scale to zero after 5 min; Launch $0.106/CU-hr, $0.35/GB; pgvector on all plans [116]. | $0–5 | n/a |
| University server | No cash cost; IT review, SSO, campus patch cycles. | $0 | Trivial (cron/systemd); slower deploys. |

**Recommendation.** Render or Railway for least ops; Fly.io plus Neon for Docker-comfortable teams wanting per-job render Machines. Store PDFs in R2 or Tigris either way, since PDF downloads drive egress. A university server is a reasonable later home, especially if campus IT wants syllabi on-premises.

## 6. AI recommendation layer

**Pipeline.**
1. An LLM with a fixed JSON schema parses the uploaded outline into weeks or units (topics, genres, assignments).
2. Embed units and catalog items (title, abstract, TOC, chapter summaries, skin tags).
3. Retrieve with hybrid search: pgvector similarity plus Postgres full-text, merged by Reciprocal Rank Fusion, as the pgvector README suggests [122].
4. Filter on hard constraints: license, length, term weeks, skin.
5. Have the LLM sequence and justify picks using only catalog IDs it was given, so it cannot invent readings.

**Storage.** pgvector 0.8.7 supports HNSW and IVFFlat. Indexes cap at 2,000 dimensions for `vector` and 4,000 for `halfvec` [122]. It is available on Supabase, Neon, Fly Managed Postgres, and Render [115][116][103][109]. sqlite-vec is still pre-v1 ("expect breaking changes") [123]. At a few thousand items either is fast enough; use pgvector since the app will run on Postgres.

**Models.** Anthropic offers no embedding model and points to Voyage AI. `voyage-4-lite` is the low-cost option, and `voyage-4-nano` is open-weight (Apache 2.0) for self-hosting [124]. Self-hosting keeps syllabi off third-party servers.

**Privacy.** FERPA covers records "directly related to a student" [125]. A course outline usually isn't one, so the risk is low. Still:
- Outlines sometimes list student or TA names. Detect and strip them, or warn, before any external call.
- Syllabus ownership varies by campus: AAUP holds that faculty own syllabi, and some systems claim work-for-hire [126]. State that uploads are used only to generate recommendations and can be deleted.
- Use API terms that bar training on inputs (Anthropic's Commercial Terms do [127]) and confirm the provider's retention window.
- Discard raw uploads after the session unless the user saves the course.

## Open items

- Writing Spaces Terms of Use and per-volume license pages (the site returned HTTP 500; PDFs returned 403).
- WAC Clearinghouse per-title licenses, which exist only in page text and PDFs.
- How Moodle and D2L handle Canvas-flavored CC 1.1. Needs real import tests.
- Schoology CC support. Generic-CC handling in Blackboard Ultra.
- DOAB/OAPEN REST access (403); harvesting terms for Pressbooks and LibreTexts.

## Sources

1. https://open.umn.edu/opentextbooks/api-docs
2. https://pressbooks.org/dev-docs/rest-api/
3. https://pressbooks.directory/opds
4. https://github.com/LibreTexts/conductor/blob/HEAD/server/api.js
5. https://openstax.org/apps/cms/api/v2/pages/?type=books.Book
6. https://openstax.org/books/writing-guide/pages/preface
7. https://oapen.org/librarians/15635975-metadata
8. https://www.doabooks.org/en/doab/metadata-harvesting-and-content-dissemination
9. https://doaj.org/api/docs
10. https://help.openalex.org/
11. https://www.crossref.org/documentation/retrieve-metadata/rest-api/
12. https://api.core.ac.uk/docs/v3
13. https://www.gutenberg.org/policy/robot_access.html
14. https://www.mediawiki.org/wiki/API:Etiquette
15. https://mesh-research.github.io/knowledge-commons-works/reference/api.html
16. https://wacclearinghouse.org/sitemap_index.xml
17. http://docs.oercommons.org/api/
18. https://info.merlot.org/merlothelp/MERLOT_Technologies.htm
19. https://www.opensyllabus.org
20. https://dashboard.wikiedu.org/training_modules.json
21. https://oasis.geneseo.edu/about.php
22. https://www.imsglobal.org/cc/index.html
23. https://www.imsglobal.org/cc/ccv1p1/imscc_profilev1p1-Overview.html
24. https://www.imsglobal.org/cc/ccv1p3/imscc_Implementation-v1p3.html
25. https://www.imsglobal.org/cc/CCv1p0thin/ims_thinCC_impl-v1p0.html
26. https://www.imsglobal.org/spec/cc/v1p4
27. https://github.com/instructure/canvas-lms/tree/master/lib/cc and https://raw.githubusercontent.com/instructure/canvas-lms/master/lib/canvas/migration/package_identifier.rb
28. https://community.instructure.com/en/kb/articles/660732-how-do-i-import-content-from-common-cartridge-into-canvas
29. https://github.com/moodle/moodle/tree/main/public/backup/converter
30. https://help.anthology.com/blackboard/instructor/en/course-and-content-management/reuse-content/import-course-packages.html and https://self-hosting.blackboard.com/Learn/Administrator/Hosting/Course_Management/Common_Cartridge_Course_Packages.html
31. https://help.anthology.com/blackboard/instructor/en/original-course-view.html
32. https://help.anthology.com/blackboard/instructor/en/migration-and-conversion/migrate-from-canvas.html
33. https://community.d2l.com/brightspace/kb/articles/4774-course-import-and-course-conversion-support
34. https://raw.githubusercontent.com/sakaiproject/sakai/master/lessonbuilder/tool/src/java/org/sakaiproject/lessonbuildertool/cc/PrintHandler.java
35. https://hub.modernclassrooms.org/help/lms-import/schoology (third-party)
36. https://developers.google.com/workspace/classroom/reference/rest
37. https://raw.githubusercontent.com/instructure/canvas-lms/master/lib/cc/module_meta.rb
38. https://raw.githubusercontent.com/instructure/canvas-lms/master/gems/canvas_sanitize/lib/canvas_sanitize/canvas_sanitize.rb
39. https://moodledev.io/docs/apis/subsystems/backup
40. https://raw.githubusercontent.com/tsugiproject/tsugi-php/master/src/Util/CC.php
41. https://github.com/pressbooks/pressbooks/tree/dev/inc/modules/export/thincc
42. https://github.com/jasp-nerd/courseforge
43. https://github.com/canvas-course-kit/canvas-course-kit
44. https://github.com/gpoore/text2qti
45. https://rubygems.org/api/v1/gems/canvas_cc.json
46. https://www.imsglobal.org/spec/lti-dl/v2p0
47. https://developerdocs.instructure.com/services/canvas/external-tools/lti/placements/file.placements_overview.md
48. https://github.com/Cvmcosta/ltijs
49. https://github.com/packbackbooks/lti-1-3-php-library
50. https://pypi.org/pypi/PyLTI1p3/json
51. https://community.instructure.com/en/kb/articles/660736-how-do-i-import-thin-common-cartridge-files-as-separate-modules
52. https://doc.courtbouillon.org/weasyprint/stable/changelog.html
53. https://doc.courtbouillon.org/weasyprint/stable/api_reference.html
54. https://www.princexml.com/purchase/
55. https://www.princexml.com/doc/prince-output/
56. https://docraptor.com/signup
57. https://docraptor.com/documentation/article/6637003-accessible-tagged-pdfs
58. https://docs.pressbooks.org/user-docs/installation
59. https://pagedjs.org/posts/en/kickstarting-the-next-iteration-of-paged.js/
60. https://registry.npmjs.org/pagedjs-cli
61. https://github.com/vivliostyle/vivliostyle-cli
62. https://typst.app/blog/2025/typst-0.14/
63. https://typst.app/docs/reference/pdf/
64. https://pandoc.org/MANUAL.html
65. https://latex3.github.io/tagging-project/
66. https://pptr.dev/api/puppeteer.pdfoptions
67. https://playwright.dev/docs/api/class-page
68. https://github.com/w3c/epubcheck/releases
69. https://daisy.github.io/ace/
70. https://creativecommons.org/licenses/by/4.0/legalcode.en
71. https://creativecommons.org/licenses/by-nd/4.0/legalcode.en
72. https://creativecommons.org/licenses/by/3.0/legalcode
73. https://creativecommons.org/licenses/by-nd/3.0/legalcode
74. https://creativecommons.org/licenses/by-nc/4.0/legalcode.en
75. https://creativecommons.org/faq/
76. https://wiki.creativecommons.org/wiki/Wiki/cc_license_compatibility
77. https://wiki.creativecommons.org/wiki/Best_practices_for_attribution
78. https://wiki.creativecommons.org/wiki/Recommended_practices_for_attribution
79. https://wiki.creativecommons.org/wiki/License_Versions
80. https://creativecommons.org/publicdomain/mark/1.0/
81. https://creativecommons.org/2018/04/02/recent-u-s-legal-decision-reinforces-strength-cc-licenses/
82. https://creativecommons.org/2020/01/07/u-s-appellate-court-enforces-ccs-interpretation-of-noncommercial/
83. https://www.princexml.com/license/
84. https://pressbooks.pub/guide/chapter/clone-a-book
85. https://kb.wisc.edu/remote-instruction/page.php?id=84558
86. https://pressbooks.palomar.edu/guide/chapter/import/ (network copy of the Pressbooks user guide)
87. https://chem.libretexts.org/Courses/Remixer_University/A_Quick_Guide_to_LibreTexts_for_New_Instructors/01%3A_Introduction_to_LibreTexts/1.02%3A_Project_Overview
88. https://chem.libretexts.org/Courses/Remixer_University/Construction_Guide_for_LibreTexts_2e/03%3A_Preliminary_Book_Design/3.02%3A_Building_Remixing_Maps
89. https://oercommons.org/about-hubs
90. https://help.openstax.org/s/article/Downloading-the-OpenStax-Canvas-Cartridge-on-Canvas-Commons
91. https://kb.wisconsin.edu/dle/143063
92. https://rebus.foundation/
93. https://research.umn.edu/news/open-source-digital-publishing-platform-fuels-scholarship-education
94. https://publishing.gmu.edu/whos-using-the-mason-oer-metafinder/
95. https://opencourselibrary.org/about/
96. https://albany.atlassian.net/wiki/spaces/askit/pages/52377175/Lumen+Learning+-+Candela+OHM+Waymaker (secondary)
97. https://kb.wisc.edu/luwmad/121810
98. https://library.villanova.edu/using-the-library/services/faculty-services/social-annotation-software-and-library-resources
99. https://galaxy.opensyllabus.org
100. https://wp.dailybruin.com/2025/03/02/ucla-professors-strive-to-make-course-materials-accessible-introduce-ai-textbooks
101. https://exlibrisgroup.com/products/leganto-reading-list-management-system/
102. https://docs.fly.io/about/pricing
103. https://fly.io/docs/mpg
104. https://docs.fly.io/launch/autostop-autostart
105. https://www.tigrisdata.com/pricing/
106. https://render.com/pricing
107. https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses
108. https://render.com/docs/free
109. https://render.com/docs/postgresql-extensions
110. https://railway.com/pricing
111. https://docs.railway.com/reference/cron-jobs
112. https://vercel.com/pricing
113. https://vercel.com/docs/functions/limitations
114. https://supabase.com/pricing
115. https://supabase.com/docs/guides/database/extensions/pgvector
116. https://neon.com/pricing
117. https://developers.cloudflare.com/workers/platform/pricing/
118. https://developers.cloudflare.com/r2/pricing/
119. https://developers.cloudflare.com/browser-rendering/pricing/
120. https://developers.cloudflare.com/browser-rendering/quick-actions/pdf-endpoint/
121. https://developers.cloudflare.com/vectorize/platform/pricing/
122. https://github.com/pgvector/pgvector
123. https://github.com/asg017/sqlite-vec
124. https://platform.claude.com/docs/en/build-with-claude/embeddings
125. https://studentprivacy.ed.gov/faq/what-education-record
126. https://www.aaup.org/sites/default/files/intellectual_property_issues_faculty.pdf
127. https://www.anthropic.com/legal/commercial-terms
