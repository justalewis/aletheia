# Sample course arcs

One YAML file per arc. Aletheia reads these to lay out a course, scale it to a term, and seed each unit's checklist.

## Schema

```yaml
id: fyc-cultural-rhetorics          # kebab-case, unique, permanent
focus: FYC                          # a focus code from pack.yaml
title: Cultural Rhetorics
tagline: One sentence shown on the arc card.
rationale: >                        # one paragraph, instructor-facing: what the arc is for and how it moves
  ...
course_level: "100"                 # 100 | 200 | 300-400 | mixed
fits_courses: [oc-engl101, wwu-eng101]   # outcome-file ids this arc suits (optional)
frameworks: [wpa, acrl]             # outcome frameworks the arc aligns to
units:
  - id: story-and-relationality     # unique within the arc
    title: Story and Relationality
    summary: One or two sentences, instructor-facing.
    goals:                          # 2-4 short student-facing goals ("Students will...") 
      - ...
    outcomes: [wpa:rhetorical-knowledge, wpa:critical-thinking-reading-composing]
    topics: [storytelling, cultural rhetorics, literacy narrative]   # catalog topic vocabulary
    weeks: {min: 2, ideal: 3}       # ideal is written against a 16-week semester
    priority: core                  # core | optional (optional units drop first in short terms)
    merge_with: next                # next | previous | null: which neighbor it can fold into
    readings:
      core: [catalog-id, ...]       # 2-4 items that carry the unit; always kept
      optional: [catalog-id, ...]   # 3-8 more, added as the pages-per-week budget allows
    assignments:
      major: [assignment-id]        # 0-1 major project for the unit (some units only build toward one)
      short_term_variant: assignment-id-or-null   # a lighter version for 5- and 8-week terms
      activities: [assignment-id, ...]            # 1-4 scaffolding or in-class activities
    notes: Optional instructor note (sequencing tip, content caution, etc.)
```

## Rules

- Every `readings` and `assignments` id must exist in `readings.jsonl` / `assignments.jsonl`.
- Core readings should be redistributable (`reuse_tier` adapt, adapt-nc, or verbatim) with `license_verified: true`, so the reader can include them. Link-only items may appear as optional readings.
- Prefer chapter- and article-level items over whole books.
- Units' `ideal` weeks plus the term's fixed weeks should total about 16 for a semester arc.
- Text fields are instructor-facing and are written in Justin's documentation voice.
- Check an arc with `python tools/check_arcs.py corpus/rhetcomp/arcs/<file>.yaml`. It fails on unknown ids and bad values and warns on the soft rules above. The backend test suite runs it over every arc.
