"""Native profile tool registration for shared Council materials."""


def register(ctx):
    from council_workspace.client import register as register_workspace

    register_workspace(ctx)
