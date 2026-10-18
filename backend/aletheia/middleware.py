from django.http import HttpResponse


class HealthCheckMiddleware:
    """Answer /healthz before Django validates the Host header.

    Fly.io's health checks reach the machine directly, not through the public hostname,
    so they would otherwise fail ALLOWED_HOSTS.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)
