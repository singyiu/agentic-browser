"""Canonical parental-control rubric (single source of truth).

Embedded in the classifier's system prompt. Edit here to change the policy.

NOTE: a formal Claude Code skill (claude-config/skills/parental-control/SKILL.md) would
also expose this rubric to interactive `claude` parent-review sessions, but the repo's
doc hook blocks creating .md files. Add that SKILL.md by hand (or relax the hook for
`skills/` paths) if you want the interactive-skill surface.
"""

from __future__ import annotations

RUBRIC = """\
Decide whether a web page is appropriate for a 10-year-old. Be conservative but
FAIL-OPEN: block clear violations, but when genuinely unsure, ALLOW with confidence
below 0.6 (over-blocking harms a child's learning and everyday browsing).

ALWAYS BLOCK (high confidence):
- adult_content: sexual, nude, or explicit material; porn; escort/hookup sites.
- graphic_violence: gore, torture, graphic injury, real-world death/abuse imagery.
- self_harm: promoting suicide, self-harm, or eating disorders.
- hate: slurs or content demeaning people by race, religion, gender, etc.
- illegal_dangerous: drug/weapon manufacture or instructions for serious harm.

BLOCK (confidence >= 0.85):
- gambling: real-money betting, casino, cash loot boxes.
- alcohol_tobacco_vaping: promotion or sale.
- harassment: communities centered on bullying or doxxing.

USE JUDGMENT (lean ALLOW for educational context):
- mature_themes: war, historical atrocity, dark fiction -> allow if educational.
- scary: horror/jump-scare -> block only if clearly intense for a child.
- dating: block dating/hookup apps; allow general social platforms.

NEVER BLOCK:
- Education, news (even hard topics), and general entertainment unless a block category
  clearly applies; search engines, encyclopedias, and children's sites.
"""
