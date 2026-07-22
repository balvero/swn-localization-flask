"""Auth shim — deliberately temporary.

Netlify Identity verified the JWT on Netlify's edge before a function ever
ran, handing it a ready-made user via context.clientContext.user. There's
no equivalent here yet since this project isn't tied to any specific auth
provider — real auth (Supabase Auth is the leading candidate, see project
notes) is a separate, later step, kept independent of porting the routes
themselves.

For now AUTH_MODE=mock (the only mode implemented) treats every request as
a fixed user, exactly like the old NETLIFY functions' LOCAL_DEV bypass, so
every route can be built and tested before auth is decided. Every route
calls require_user(request) exactly once, the same as the old
requireUser(context) — swapping in real auth later only means changing
this one function, not every route.
"""

import os

MOCK_USER = {"email": "local-dev@example.com"}


def require_user(request):
    mode = os.environ.get("AUTH_MODE", "mock")
    if mode == "mock":
        return MOCK_USER
    raise RuntimeError(f"AUTH_MODE={mode!r} is not implemented yet — only 'mock' exists so far")
