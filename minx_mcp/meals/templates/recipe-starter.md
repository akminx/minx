---
title: Your Recipe Title
tags: [weeknight, high-protein]
prep_time: 10 min
cook_time: 15 min
servings: 2
source: 
image: 
---

# Your Recipe Title

## Ingredients
- 2 eggs
- 2 slices sourdough bread
- 1 tbsp butter
- Salt and pepper to taste (optional)

## Substitutions
- butter: olive oil, ghee
- sourdough bread: whole wheat bread

## Notes
Replace the fields above with your own recipe. A few authoring tips so the Meals
indexer picks this up cleanly:

- Keep the section headings exactly as shown: `## Ingredients`, `## Substitutions`, `## Notes` (with a `# Title` heading at the top of the body).
- Format ingredients as `- <quantity+unit> <name>` bullets. Append `(optional)` to non-required items.
- Format substitutions as `- <original>: <alt-1>, <alt-2>` bullets.
- `tags:` must use YAML list syntax: `[tag-a, tag-b]`.
- `prep_time:` / `cook_time:` accept values like `10 min` or just a number.
- Embed an Obsidian image link below the title if you have one, using Obsidian's embed syntax pointing at an asset path like `Assets/dish.png` (the Meals indexer picks the first embed in the body as the recipe image).
