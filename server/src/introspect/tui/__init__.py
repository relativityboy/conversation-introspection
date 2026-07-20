"""The ``introspect tui`` terminal interface (spec §16).

Import-light by policy: this package's submodules pull in ``textual`` (:mod:`introspect.tui.app`)
only when the ``introspect tui`` verb runs. The CLI imports :func:`introspect.tui.app.run_tui`
lazily inside its handler so cron's ``introspect import`` never pays the TUI's import cost.
Nothing is re-exported here to keep ``import introspect.tui`` free of ``textual``.
"""
