"""R3 GitHub integration: project repository scaffolding, CI workflow
templates, revision pinning (``r3.lock.yaml``) and explicit remote creation."""

from .lock import pin, read_lock
from .scaffold import DEFAULT_PLATFORM_URL, create_project, create_remote, render_workflow

__all__ = ["DEFAULT_PLATFORM_URL", "create_project", "create_remote", "pin", "read_lock", "render_workflow"]
