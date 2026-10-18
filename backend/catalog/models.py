"""Catalog models: everything loaded from a corpus pack.

These tables are a read-only projection of corpus/<pack>/ in the repo; `manage.py load_pack`
rebuilds them. Edit the pack files, not these rows.
"""
from django.db import models


class Pack(models.Model):
    id = models.SlugField(primary_key=True)
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=40)
    config = models.JSONField(default=dict)  # pack.yaml: focuses, reuse tiers, term presets
    loaded_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Source(models.Model):
    id = models.CharField(primary_key=True, max_length=120)
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="sources")
    name = models.CharField(max_length=300)
    url = models.URLField(max_length=1000, blank=True)
    type = models.CharField(max_length=80, blank=True)
    data = models.JSONField(default=dict)

    def __str__(self):
        return self.name


class LicensedRecord(models.Model):
    """Fields shared by readings and assignments."""

    REUSE_TIERS = [
        ("adapt", "Adapt freely"),
        ("adapt-nc", "Adapt (noncommercial)"),
        ("verbatim", "Use whole and unchanged"),
        ("link-only", "Link only"),
    ]

    id = models.CharField(primary_key=True, max_length=160)
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE)
    title = models.TextField()
    authors = models.TextField(blank=True)
    year = models.CharField(max_length=40, blank=True)
    license = models.CharField(max_length=300, blank=True)
    license_family = models.CharField(max_length=60, blank=True)
    license_url = models.URLField(max_length=1000, blank=True)
    license_verified = models.BooleanField(default=False)
    reuse_tier = models.CharField(max_length=12, choices=REUSE_TIERS, default="link-only")
    skins = models.JSONField(default=list)
    topics = models.JSONField(default=list)
    course_level = models.CharField(max_length=40, blank=True)
    summary = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    withdrawn = models.BooleanField(default=False)
    # Flattened copies for portable filtering (SQLite can't query inside JSON lists).
    skins_text = models.TextField(blank=True)   # "|FYC|RESEARCH|"
    topics_text = models.TextField(blank=True)  # "|audience|genre|"
    search_text = models.TextField(blank=True)  # lowercased title, authors, parent, summary, topics

    class Meta:
        abstract = True

    def fill_index_fields(self):
        self.skins_text = "|" + "|".join(self.skins) + "|" if self.skins else ""
        self.topics_text = "|" + "|".join(t.lower() for t in self.topics) + "|" if self.topics else ""
        parts = [self.title, self.authors, getattr(self, "parent_title", ""), self.summary, " ".join(self.topics)]
        self.search_text = " ".join(p for p in parts if p).lower()

    @property
    def effective_tier(self) -> str:
        """Unverified licenses are treated as link-only until someone checks them."""
        return self.reuse_tier if self.license_verified else "link-only"

    @property
    def redistributable(self) -> bool:
        return self.effective_tier != "link-only"

    def __str__(self):
        return self.title


class Reading(LicensedRecord):
    LEVELS = ["collection", "website", "journal", "book", "chapter", "article", "primary-text"]

    level = models.CharField(max_length=20, db_index=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    parent_title = models.TextField(blank=True)
    publisher_platform = models.CharField(max_length=300, blank=True)
    landing_url = models.URLField(max_length=1000, blank=True)
    html_url = models.URLField(max_length=1000, blank=True)
    pdf_url = models.URLField(max_length=1000, blank=True)
    epub_url = models.URLField(max_length=1000, blank=True)
    pages = models.PositiveIntegerField(null=True, blank=True)
    audience = models.CharField(max_length=40, blank=True)

    @property
    def best_url(self) -> str:
        return self.html_url or self.landing_url or self.pdf_url or self.epub_url


class Assignment(LicensedRecord):
    source_repository = models.CharField(max_length=300, blank=True)
    url = models.URLField(max_length=1000, blank=True)
    file_url = models.URLField(max_length=1000, blank=True)
    assignment_type = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=60, blank=True, db_index=True)
    estimated_weeks = models.CharField(max_length=100, blank=True)
    pairs_with_readings = models.JSONField(default=list)

    @property
    def best_url(self) -> str:
        return self.url or self.file_url


class OutcomeSet(models.Model):
    """A framework (WPA OS 3.0, ACRL) or one institution's course outcomes."""

    id = models.CharField(primary_key=True, max_length=80)
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="outcome_sets")
    kind = models.CharField(max_length=20, default="course")  # course | framework
    institution = models.CharField(max_length=200, blank=True)
    course = models.CharField(max_length=40, blank=True)
    title = models.CharField(max_length=300)
    data = models.JSONField(default=dict)

    def __str__(self):
        return f"{self.institution} {self.course} {self.title}".strip()


class Outcome(models.Model):
    id = models.CharField(primary_key=True, max_length=120)
    outcome_set = models.ForeignKey(OutcomeSet, on_delete=models.CASCADE, related_name="outcomes")
    order = models.PositiveIntegerField(default=0)
    text = models.TextField()
    frameworks = models.JSONField(default=list)
    topics = models.JSONField(default=list)

    class Meta:
        ordering = ["outcome_set", "order"]


class Arc(models.Model):
    id = models.SlugField(primary_key=True, max_length=120)
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="arcs")
    focus = models.CharField(max_length=20, db_index=True)
    title = models.CharField(max_length=200)
    tagline = models.TextField(blank=True)
    rationale = models.TextField(blank=True)
    course_level = models.CharField(max_length=40, blank=True)
    frameworks = models.JSONField(default=list)
    fits_courses = models.JSONField(default=list)

    def __str__(self):
        return self.title


class Unit(models.Model):
    arc = models.ForeignKey(Arc, on_delete=models.CASCADE, related_name="units")
    key = models.SlugField(max_length=120)
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    goals = models.JSONField(default=list)
    outcomes = models.JSONField(default=list)
    topics = models.JSONField(default=list)
    min_weeks = models.PositiveIntegerField(default=1)
    ideal_weeks = models.PositiveIntegerField(default=2)
    priority = models.CharField(max_length=10, default="core")  # core | optional
    merge_with = models.CharField(max_length=10, blank=True)  # next | previous | ""
    notes = models.TextField(blank=True)
    readings = models.ManyToManyField(Reading, through="UnitReading")
    assignments = models.ManyToManyField(Assignment, through="UnitAssignment")

    class Meta:
        ordering = ["arc", "order"]
        unique_together = [("arc", "key")]


class UnitReading(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE)
    reading = models.ForeignKey(Reading, on_delete=models.CASCADE)
    role = models.CharField(max_length=10)  # core | optional
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["unit", "role", "order"]


class UnitAssignment(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE)
    role = models.CharField(max_length=12)  # major | short_term | activity
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["unit", "role", "order"]
