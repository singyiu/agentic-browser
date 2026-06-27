"""Pluggable headless AI backends for the guardian classifier.

A :class:`~agent_backend.guardian.ai.backend.CompletionBackend` turns a (system prompt,
user prompt) pair into text. ``Classifier`` owns all prompt assembly and verdict parsing and
delegates only the model call here, so swapping Claude for Codex never touches the routes.
"""
