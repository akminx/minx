from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from minx_mcp.meals.pantry import normalize_ingredient


@dataclass(frozen=True)
class ParsedIngredient:
    display_text: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    is_required: bool
    sort_order: int


@dataclass(frozen=True)
class ParsedSubstitution:
    original_name: str
    substitute_name: str
    display_text: str
    priority: int


@dataclass(frozen=True)
class ParsedRecipe:
    title: str
    tags: list[str]
    prep_time_minutes: int | None
    cook_time_minutes: int | None
    servings: int | None
    source_url: str | None
    image_ref: str | None
    ingredients: list[ParsedIngredient]
    substitutions: list[ParsedSubstitution]
    content_hash: str
    notes: str | None
    nutrition_summary: dict[str, object] | None = None


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
_GROUP_RE = re.compile(r"^\*\*.+:\*\*:?$")
_IMAGE_RE = re.compile(r"!\[\[([^]]+)\]\]")
_LEADING_AMOUNT_RE = re.compile(
    r"^(?:\d+(?:/\d+)?|\d+(?:\.\d+)?)(?:\s*)"
    r"(?:g|kg|mg|oz|lb|ml|l|cup|cups|tbsp|tsp|servings?|slices?|cans?)?\s+",
    re.IGNORECASE,
)


def parse_recipe_note(path: Path) -> ParsedRecipe:
    content = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(content)
    metadata = _parse_frontmatter(frontmatter)
    title = str(metadata.get("title") or _title_from_body(body) or path.stem)
    image_ref = _string_or_none(metadata.get("image")) or _first_obsidian_image(body)
    ingredients = _parse_ingredients(_section_lines(body, "Ingredients"))
    substitutions = _parse_substitutions(_section_lines(body, "Substitutions"))
    notes_text = "\n".join(_section_lines(body, "Notes")).strip()
    nutrition_summary = _parse_nutrition_from_frontmatter(frontmatter)
    return ParsedRecipe(
        title=title,
        tags=_list_value(metadata.get("tags")),
        prep_time_minutes=_minutes_value(metadata.get("prep_time")),
        cook_time_minutes=_minutes_value(metadata.get("cook_time")),
        servings=_int_value(metadata.get("servings")),
        source_url=_string_or_none(metadata.get("source")),
        image_ref=image_ref,
        ingredients=ingredients,
        substitutions=substitutions,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        notes=notes_text or None,
        nutrition_summary=nutrition_summary,
    )


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    end = content.find("\n---", 4)
    if end == -1:
        return "", content
    frontmatter = content[4:end]
    body = content[end + len("\n---") :]
    return frontmatter, body.lstrip("\n")


def _parse_nutrition_from_frontmatter(frontmatter: str) -> dict[str, object] | None:
    """Parse optional ``nutrition`` YAML frontmatter (case-insensitive key).

    Supports a single-line JSON object after ``nutrition:`` or an indented
    ``key: value`` block on following lines.
    """
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        if key.strip().lower() != "nutrition":
            continue
        rest = rest.strip()
        if rest.startswith("{"):
            try:
                loaded = json.loads(rest)
            except json.JSONDecodeError:
                return None
            return loaded if isinstance(loaded, dict) else None
        block: dict[str, object] = {}
        for j in range(i + 1, len(lines)):
            cont = lines[j]
            if not cont.strip():
                continue
            if not cont[:1].isspace():
                break
            stripped = cont.strip()
            if ":" not in stripped:
                continue
            ik, _, iv = stripped.partition(":")
            ik = ik.strip()
            iv = iv.strip()
            if not ik:
                continue
            block[ik] = _coerce_frontmatter_scalar(iv)
        return block or None
    return None


def _coerce_frontmatter_scalar(raw: str) -> object:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _parse_frontmatter(text: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            continue
        if raw_value.startswith("[") and raw_value.endswith("]"):
            values[key] = [item.strip() for item in raw_value[1:-1].split(",") if item.strip()]
        else:
            values[key] = raw_value
    return values


def _title_from_body(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _first_obsidian_image(body: str) -> str | None:
    match = _IMAGE_RE.search(body)
    return match.group(1).strip() if match else None


def _section_lines(body: str, section_name: str) -> list[str]:
    target = section_name.lower()
    active = False
    lines: list[str] = []
    for line in body.splitlines():
        section_match = _SECTION_RE.match(line)
        if section_match:
            active = section_match.group(1).strip().lower() == target
            continue
        if active:
            lines.append(line)
    return lines


def _parse_ingredients(lines: list[str]) -> list[ParsedIngredient]:
    ingredients: list[ParsedIngredient] = []
    for line in lines:
        match = _BULLET_RE.match(line)
        if not match:
            continue
        display_text = match.group(1).strip()
        if _GROUP_RE.match(display_text):
            continue
        is_required = not display_text.lower().endswith("(optional)")
        clean = re.sub(r"\s*\(optional\)\s*$", "", display_text, flags=re.IGNORECASE)
        normalized = normalize_ingredient(_LEADING_AMOUNT_RE.sub("", clean).split(",")[0])
        ingredients.append(
            ParsedIngredient(
                display_text=display_text,
                normalized_name=normalized,
                quantity=None,
                unit=None,
                is_required=is_required,
                sort_order=len(ingredients),
            )
        )
    return ingredients


def _parse_substitutions(lines: list[str]) -> list[ParsedSubstitution]:
    substitutions: list[ParsedSubstitution] = []
    for line in lines:
        match = _BULLET_RE.match(line)
        if not match or ":" not in match.group(1):
            continue
        original, raw_subs = match.group(1).split(":", 1)
        original_name = normalize_ingredient(original)
        for raw_sub in raw_subs.split(","):
            substitute = normalize_ingredient(raw_sub)
            if not substitute:
                continue
            substitutions.append(
                ParsedSubstitution(
                    original_name=original_name,
                    substitute_name=substitute,
                    display_text=raw_sub.strip(),
                    priority=len(substitutions),
                )
            )
    return substitutions


def _list_value(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _minutes_value(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _int_value(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
