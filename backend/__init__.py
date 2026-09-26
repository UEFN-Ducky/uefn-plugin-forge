"""Ducky Forge — a modeler inside UEFN-Ducky."""

from __future__ import annotations


def register(api) -> None:
    from .panel_rpc import register_panel_rpcs
    from .tools import register_tools

    register_tools(api)
    register_panel_rpcs(api)
    api.log("forge tools registered")
