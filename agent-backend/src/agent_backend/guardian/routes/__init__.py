"""Route sub-package for the guardian service.

Each module exports ``build_routes(deps: GuardianDeps) -> list[Route]``.
``service.py`` assembles the full route list from these builders.
"""
