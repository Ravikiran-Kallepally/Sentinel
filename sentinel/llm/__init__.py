"""Provider-neutral LLM layer.

Application code never imports a vendor SDK. It calls `get_router().complete(task=..., ...)`, and the router
picks the provider and model for that task, retries, falls back, and records cost. Swapping vendors, adding
a cheaper model for one task, or running fully offline is a configuration change, not a code change.
"""

from sentinel.llm.router import get_router  # noqa: F401
