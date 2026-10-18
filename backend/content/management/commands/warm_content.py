"""Fill the content cache with the full text of the readings the sample arcs use.

    python manage.py warm_content                      # every reading in corpus/<pack>/arcs/*.yaml
    python manage.py warm_content --arc tc-ux-focus    # one arc (repeatable)
    python manage.py warm_content --ids a-reading another-reading
    python manage.py warm_content --pdf-dir "D:/WAC PDFs"   # extra folder of PDFs (repeatable)
    python manage.py warm_content --offline            # local PDFs only, no network
    python manage.py warm_content --refresh            # re-fetch readings already cached

Readings already cached are kept unless --refresh. The run writes _report.json in the cache
and, for PDFs behind a bot check, needs_pdf.csv: download those in a browser, save each as
<reading-id>.pdf in a folder, and pass the folder with --pdf-dir. Run `load_pack` first.
"""
import csv
import io
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from catalog.models import Reading
from content.fetch import Client
from content.library import PdfLibrary
from content.store import ContentStore
from content.warm import Warmer, reading_ids_in_arcs, summarize

GATED = {"bot-gated", "blocked", "robots"}


class Command(BaseCommand):
    help = "Cache the full text of readings used in the sample arcs."

    def add_arguments(self, parser):
        parser.add_argument("--pack", default="rhetcomp")
        parser.add_argument("--arc", action="append", default=[], help="arc id (default: all arcs)")
        parser.add_argument("--ids", nargs="+", default=[], help="warm these reading ids instead of arcs")
        parser.add_argument("--pdf-dir", action="append", default=[], type=Path,
                            help="extra folder of PDFs, searched before the default corpus/files folders")
        parser.add_argument("--offline", action="store_true", help="use local PDFs only")
        parser.add_argument("--refresh", action="store_true", help="re-fetch readings already cached")
        parser.add_argument("--no-images", action="store_true", help="don't download images for HTML readings")
        parser.add_argument("--delay", type=float, default=2.0, help="seconds between requests to one host")

    def handle(self, *args, pack, arc, ids, pdf_dir, offline, refresh, no_images, delay, **opts):
        if ids:
            wanted = list(dict.fromkeys(ids))
        else:
            folder = Path(settings.CORPUS_DIR) / pack / "arcs"
            files = sorted(folder.glob("*.yaml"))
            if arc:
                files = [f for f in files if f.stem in set(arc)]
                if len(files) != len(set(arc)):
                    raise CommandError(f"unknown arc in {arc}; arcs are {[f.stem for f in folder.glob('*.yaml')]}")
            wanted = reading_ids_in_arcs(files)
        rows = {r.id: r for r in Reading.objects.filter(id__in=wanted)}
        if not rows:
            raise CommandError("No matching readings in the database. Run `manage.py load_pack` first.")
        missing = [i for i in wanted if i not in rows]
        readings = [rows[i] for i in wanted if i in rows]

        store = ContentStore()
        library = PdfLibrary(list(pdf_dir) + list(settings.CONTENT_PDF_DIRS))
        warmer = Warmer(store, Client(delay=delay), library, offline=offline, images=not no_images,
                        log=lambda m: self.stdout.write(m))
        warmer.prepare(readings, list(Reading.objects.exclude(pdf_url="").values_list("pdf_url", flat=True)))
        self.stdout.write(f"{len(readings)} readings; PDF folders: {[str(f) for f in library.folders] or 'none'}")

        outcomes = []
        for n, r in enumerate(_by_host(readings), 1):
            o = warmer.warm(r, refresh=refresh)
            outcomes.append(o)
            line = f"[{n}/{len(readings)}] {o.status:12s} {o.kind or '-':4s} {o.via or '':10s} {r.id}"
            if o.detail and not o.ok:
                line += f"  ({o.detail[:160]})"
            self.stdout.write(line)

        report = summarize(outcomes)
        report["unknown_ids"] = missing
        store.write_report(report)
        gated = [o for o in outcomes if not o.ok and o.pdf_url and (o.status in GATED or "bot-gated" in o.detail)]
        if gated:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["save_as", "title", "pdf_url"])
            for o in gated:
                w.writerow([f"{o.id}.pdf", o.title, o.pdf_url])
            store.storage.save("needs_pdf.csv", ContentFile(buf.getvalue().encode("utf-8-sig")))

        self.stdout.write(self.style.SUCCESS(f"done: {report['counts']}"))
        if gated:
            self.stdout.write(f"{len(gated)} PDFs are behind a bot check; see needs_pdf.csv in the cache.")
        for i in missing:
            self.stdout.write(self.style.WARNING(f"unknown reading id: {i}"))


def _by_host(readings):
    """Interleave hosts so the per-host delay rarely makes the run wait."""
    from urllib.parse import urlsplit

    queues: dict[str, list] = {}
    for r in readings:
        queues.setdefault(urlsplit(r.html_url or r.pdf_url or "").netloc, []).append(r)
    out = []
    while queues:
        for host in list(queues):
            out.append(queues[host].pop(0))
            if not queues[host]:
                del queues[host]
    return out
