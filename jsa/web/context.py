"""What the dashboard's route modules share (plan 24).

`create_app` builds one `Ctx` per app, holding the helpers that close over
its tracker, output folder and profile loader, and hands it to every route
module's `register(app, ctx)`.
"""

from types import SimpleNamespace


class Ctx(SimpleNamespace):
    """connect(), out_dir(), profile(), render(), not_found(), the back-to
    redirects, and the Matches/Turbo deck helpers."""
