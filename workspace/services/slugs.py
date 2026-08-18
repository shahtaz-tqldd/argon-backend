from django.utils.text import slugify


def generate_workspace_slug(name, *, workspace_id=None):
    """Return a URL-safe, currently unused slug for a workspace name."""
    from workspace.models import Workspace

    base_slug = slugify(name)[:120].strip("-") or "workspace"
    queryset = Workspace.objects.all()
    if workspace_id:
        queryset = queryset.exclude(pk=workspace_id)

    candidate = base_slug
    suffix = 2
    while queryset.filter(slug=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base_slug[: 140 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate
