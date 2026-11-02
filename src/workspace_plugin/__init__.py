"""Native profile tool registration for shared Council materials."""


def register(ctx):
    from council_workspace.client import register as register_workspace

    register_workspace(ctx)
    from council_recurring.client import register as register_recurring

    register_recurring(ctx)

    from council_workspace.materials import register as register_materials

    register_materials(ctx)
