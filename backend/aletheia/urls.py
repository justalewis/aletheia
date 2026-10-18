from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, HttpResponse
from django.urls import path, re_path
from ninja import NinjaAPI

from builder.api import router as builder_router
from catalog.api import router as catalog_router
from feedback.api import router as feedback_router
from programs.api import router as programs_router

api = NinjaAPI(title="Aletheia API", version="0.1.0")
api.add_router("/", catalog_router)
api.add_router("/", builder_router)
api.add_router("/", programs_router)
api.add_router("/", feedback_router)
api.add_router("/", "catalog.outcomes_api.router")


def spa(request, *args, **kwargs):
    """Serve the React app's index.html for every non-API route (client-side routing)."""
    index = settings.FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(open(index, "rb"), content_type="text/html")
    return HttpResponse(
        "<p>Aletheia API is running. Build the frontend (<code>npm run build</code> in frontend/) "
        "or run the Vite dev server.</p><p><a href='/api/docs'>API docs</a></p>")


def healthz(request):
    return HttpResponse("ok")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("healthz", healthz),
    re_path(r"^(?!api/|admin/|static/).*$", spa),
]
