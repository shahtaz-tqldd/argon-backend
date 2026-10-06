def get_client_ip(request):
    """Best-effort client IP for a request.

    Honors the first hop of X-Forwarded-For (set by the proxy in front of
    the API) and falls back to REMOTE_ADDR. Returns "" when nothing is
    known; callers validate the value before storing it.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate
    return (request.META.get("REMOTE_ADDR") or "").strip()
