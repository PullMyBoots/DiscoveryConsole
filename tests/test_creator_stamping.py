"""Regression checks for bundled skill metadata."""

from pathlib import Path


def test_bundled_skill_md_files_have_no_creator_frontmatter():
    """Bundled skills must not have `creator:` in their SKILL.md frontmatter, so
    agent-authored skill filters correctly exclude them."""
    bundled = Path("coral/template/skills").rglob("SKILL.md")
    for skill_md in bundled:
        text = skill_md.read_text()
        # Only inspect frontmatter (first --- block); body may legitimately mention "creator"
        if not text.startswith("---"):
            continue
        end = text.find("---", 3)
        front = text[3:end]
        assert "\ncreator:" not in front and not front.startswith("creator:"), (
            f"{skill_md} has stray `creator:` in frontmatter and would look agent-authored"
        )
