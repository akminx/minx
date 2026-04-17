# Slice 3: Meals MCP Design

**Date:** 2026-04-11
**Status:** Implemented (design retained as historical source of intent)
**Parent:** [Minx Life OS Architecture Design](2026-04-06-minx-life-os-architecture-design.md)
**Depends on:** Slice 2.5 cleanup
**First implementation slice:** Platform launcher, Meals foundation, recipe recommendation

## Purpose

Build `Meals MCP` as a first-class domain server parallel to Finance.

The practical product question is: "what should I cook today?"

Meals should make meal logs, pantry state, recipes, and nutrition data usable by Core and the harness without collapsing them into one mega-server. SousChef can inform behavior, but only when a behavior still fits Minx. It is a behavior inventory, not an architecture blueprint.

## Product Goals

- Let the user log meals and nutrition in a structured way.
- Let the user store recipes in Obsidian and have Meals index and reason over them.
- Let the user track pantry and inventory items, including expiration dates.
- Let the harness recommend recipes the user can actually cook now, or can cook with reasonable substitutions.
- Let the system generate a shopping list only when missing ingredients truly require it.
- Surface useful recipe details like image or asset links, ingredients, notes, nutrition, and source metadata.

## Non-Negotiable Design Rules

- Keep the many-server shape: Finance, Meals, Core, and future domains stay separate MCP servers.
- Add a tiny launcher/supervisor layer for convenience, but do not turn it into a mega-server.
- Make event registration explicit and enforced.
- Keep Core as a composition layer, not a finance-shaped struct with extra fields.
- Do not invent a generic `DomainAdapter` abstraction before Meals exists.
- Keep domain writes local to each domain server.
- Keep Core read-only with respect to domain facts.
- Keep deterministic signals in Core and presentation in the harness.
- Make migrations and platform helpers domain-neutral.
- Extract shared cross-domain abstractions only after Finance and Meals both exist and prove the shape.
- Use tiny registries only where they enforce existing contracts, such as event payload registration. Defer broader domain contribution registries until Meals exists.

## Scope Summary

This design covers the Meals roadmap across five phases. The next implementation plan should focus on Phase 0 through Phase 2: launcher boundary, Meals foundation, and deterministic recipe recommendation. Phase 3 shopping list generation and Phase 4 richer presentation are defined here so the earlier data model does not paint the project into a corner, but they should not broaden the first slice.

- Phase 0: platform and launcher boundary.
- Phase 1: Meals foundation.
- Phase 2: recipe recommendation and pantry-aware ranking.
- Phase 3: shopping list generation and low-stock replenishment support.
- Phase 4: richer recipe presentation and attachments.

## Phase 0: Platform Boundary

Add a tiny local launcher or supervisor that can start `minx-core`, `minx-finance`, and `minx-meals` together for local convenience.

The launcher is outside domain logic. It is not the source of truth for tool contracts, persistence, migrations, service construction, or domain orchestration. All server processes must remain independently runnable through their own entry points.

The launcher may own only process startup convenience: resolving configured command lines, starting child processes, relaying logs, and stopping children cleanly on interrupt.

## Platform Primitives

Migrations, event registration, and local server launch should feel like platform primitives, not Finance infrastructure.

For this slice, that means:

- Add Meals schema through the same numbered migration flow Finance uses, with domain-prefixed table names and no Finance-specific helper assumptions.
- Keep all migration helpers domain-neutral so later domains can use them without inheriting Finance vocabulary.
- Keep the launcher as process supervision only. It can read a small manifest of local MCP server commands, but it cannot own domain routing or business logic.
- Convert event payload registration from a single central string-to-model switch into a small registry that composes per-domain declarations.

This is not a request to build a general domain plugin system. It is a request to make the platform contracts that already exist less Finance-shaped before Meals depends on them.

The composable event registry should follow the pyeventsourcing pattern: each domain declares its event payload models in a domain-owned mapping (e.g., `FINANCE_EVENT_PAYLOADS`, `MEALS_EVENT_PAYLOADS`), and the shared event module composes them into `PAYLOAD_MODELS` at import time. This replaces the current single hardcoded dict in `events.py` without requiring a plugin framework. The existing `PAYLOAD_UPCASTERS` dict already supports per-event-type upcasting chains and needs no structural change.

## Phase 1: Meals Foundation

Create `minx_mcp/meals/` as a first-class package with a thin MCP server, service layer, SQLite persistence, Obsidian recipe indexing helpers, and a Core-facing read API.

Meals is the source of truth for:

- meal logs
- pantry and inventory state
- indexed recipe metadata and normalized ingredients
- nutrition lookup or cache state, if this remains useful
- generated shopping list records, when Phase 3 lands

Recipes themselves live as Obsidian markdown notes. Meals indexes recipe notes into structured tables so recommendation and pantry matching can be deterministic and inspectable. Recipe image references stay as vault assets or links, not binary blobs in SQLite.

### Package Shape

The first slice should prefer small, explicit modules over a broad abstraction:

- `minx_mcp/meals/__main__.py`: independently runnable server entry point.
- `minx_mcp/meals/server.py`: thin FastMCP tool definitions and input validation.
- `minx_mcp/meals/service.py`: domain write operations and orchestration.
- `minx_mcp/meals/read_api.py`: read-only Core-facing interface analogous to `FinanceReadAPI`.
- `minx_mcp/meals/models.py`: dataclasses or Pydantic models for Meals domain records and recommendation outputs.
- `minx_mcp/meals/recipes.py`: deterministic recipe note parsing and indexing helpers.
- `minx_mcp/meals/pantry.py`: ingredient normalization, pantry matching, substitutions, and inventory checks.
- `minx_mcp/meals/recommendations.py`: recipe classification, ranking, and explanation signals.

Do not create a generic domain adapter. If Finance and Meals later converge on an obvious shared pattern, extract it after both implementations exist.

### Schema

Add a Meals migration after the current numbered migrations. The migration should use domain-prefixed table names and avoid Finance assumptions:

- `meals_meal_entries`: logged meals with occurred date/time, meal kind, summary, structured food items, nutrition totals, notes, source, and timestamps.
- `meals_pantry_items`: inventory identity, display name, normalized name, quantity, unit, expiration date, low-stock threshold, source, and timestamps.
- `meals_recipes`: vault path, title, normalized title, source URL, image reference, prep time, cook time, servings, tags, notes, nutrition summary, content hash, indexed timestamp, and timestamps.
- `meals_recipe_ingredients`: recipe id, display text, normalized ingredient name, quantity, unit, required flag, ingredient group, sort order, and notes.
- `meals_recipe_substitutions`: recipe ingredient id, substitute normalized ingredient name, display text, quantity, unit, priority, and notes.
- `meals_nutrition_cache`: optional lookup cache keyed by normalized item plus source metadata.
- `meals_shopping_lists` and `meals_shopping_list_items`: Phase 3 generated-artifact metadata tables. Do not add them in the first implementation slice unless a migration compatibility decision makes that simpler than adding them later.

Shopping lists are generated artifacts, not source-of-truth inventory.

### Data Model Guidance (informed by PANTS recipe composition pattern)

The Meals data model should maintain a clear separation between three entity levels:

1. **Ingredient** — nutritional truth per normalized unit (e.g., per 100g). This is the atomic building block. Ingredients are identified by a normalized name and carry base nutrition data. This maps to the `meals_pantry_items` and `meals_recipe_ingredients` tables.
2. **Recipe** — aggregates ingredients by weight/quantity into a composite. Nutrition rolls up through composition. A recipe can reference other recipes as sub-components (e.g., a sauce used in a main dish), though recursive composition is deferred to Phase 4.
3. **LoggedMeal** — a temporal event recording what was actually consumed. References recipes or ad-hoc food items, with portion adjustment. This maps to `meals_meal_entries`.

This separation ensures that updating an ingredient's nutritional data propagates correctly through recipes and that meal logs remain stable historical records even when recipe definitions evolve.

### Events

Register every Meals event payload explicitly through the shared event registry. Unknown event types must keep failing loudly through the existing `UnknownEventTypeError` posture.

Avoid making Meals events "just another string in a central switch statement." The preferred shape is that each domain declares its own event payload models in one place, and the shared event module composes those declarations into the enforced registry.

For the first slice, a small explicit structure is enough:

- Finance declares Finance event payload models in a Finance-owned mapping.
- Meals declares Meals event payload models in a Meals-owned mapping.
- The shared event emitter reads the composed registry and raises on unknown event types.
- Tests prove unknown events fail and registered Meals payloads validate.

Initial event names:

- `meal.logged`
- `nutrition.day_updated`

Add `meal.plan_updated` only if meal planning lands in a later slice. Do not register speculative events that no code emits.

Event payloads stay typed and domain-local. Core timeline summaries should understand Meals events rather than falling back to raw event names.

### Core Read Model

Add a Meals read API interface analogous to Finance's read API. Core should assemble a daily snapshot from multiple domain contributions without taking ownership of Meals facts.

This slice should begin moving Core away from a finance-shaped snapshot that grows a new permanent field for every domain. `NutritionSnapshot` is the concrete Meals contribution for this slice, but it should be treated as the first visible domain contribution, not as proof that `DailySnapshot` should keep expanding forever.

Add `NutritionSnapshot` as a discrete field in the Core snapshot model. Do not hide nutrition under an undifferentiated `extra` field. The initial shape should be intentionally narrow:

```python
@dataclass(frozen=True)
class NutritionSnapshot:
    date: str
    meal_count: int
    protein_grams: float | None
    calories: int | None
    last_meal_at: str | None
    skipped_meal_signals: list[str]
```

Core may add a narrow set of deterministic Meals-facing detectors for:

- low protein
- meal frequency patterns
- skipped-meal patterns

Core should not become a kitchen-specific business rules engine. Recipe eligibility, substitutions, pantry matching, expiration checks, and shopping list diffs belong in Meals.

## SousChef Reuse Policy

Reuse SousChef ideas only when they improve Minx's design.

Reuse:

- `souschef/vault.py`-style pure parsing and writing helpers where they still fit.
- The SQLite initializer pattern if it stays idempotent and domain-neutral.
- The isolated temp-vault and temp-db test style.
- Meal logging and pantry update ideas after stripping old app assumptions.

Rebuild:

- The old MCP dispatcher.
- HEB automation.
- Web recipe extraction.
- Browser-driven shopping checkout.
- Other old app-specific integrations.

Do not port SousChef architecture wholesale.

## Meals Data Ownership

- Recipes live as Obsidian markdown notes and are indexed by Meals.
- Pantry and inventory state lives in Meals as structured data.
- Meal logs live in Meals as structured data.
- Nutrition lookup or cache state lives in Meals if it remains useful for this design.
- Shopping lists are generated artifacts, not source-of-truth inventory.
- Recipe image references stay as vault assets or links, not binary blobs in SQLite.

Core is read-only with respect to these facts. The harness can ask Meals to perform domain writes, but it should not mutate Meals state indirectly through Core.

## Parsing And LLM Policy

Use LiteParse through a small adapter boundary for document-to-text extraction only.

Use server-side LLMs only for bounded, structured extraction and classification. Use harness-side LLMs for user-facing narration and conversation. Deterministic code is the source of truth for:

- pantry matching
- recipe eligibility
- substitutions
- ranking
- expiration checks
- shopping list diffs

Allow dual-path tools where useful: structured input from a smart harness, or natural language input with server-side parsing fallback. Recipe import cleanup, metadata extraction, and optional substitution suggestions can happen server-side, but persistence requires schema validation before writes.

Explanations, coaching, and conversational follow-up belong in the harness.

Do not let LLMs mutate source-of-truth pantry or recipe state without an explicit validation or confirmation boundary.

### Rule-First + LLM-Fallback Pattern (informed by NumbyAI)

Ingredient normalization, pantry matching, and recipe categorization should follow a rule-first + LLM-fallback pattern:

1. **Deterministic rules fire first.** A lookup table or deterministic normalizer resolves known ingredient names, units, and categories. Rules are stored as data (in SQLite), not hardcoded, so they grow as the user adds recipes and pantry items.
2. **LLM handles the remainder.** When deterministic rules produce no match (e.g., a novel ingredient spelling, an ambiguous unit), the LLM path attempts structured extraction.
3. **Successful LLM resolutions can be promoted to rules.** When the LLM successfully normalizes an ingredient and the user confirms it (implicitly by not correcting), the mapping can be persisted as a deterministic rule for future use. This makes the system self-improving over time.
4. **Conflict detection.** If a new rule would contradict an existing one, surface it as a clarification rather than silently overwriting.

This pattern is already proven in the Finance dual-path tools (`finance_query`, `goal_parse`). Meals should follow the same shape: structured input from a smart harness, or NL input with server-side parsing fallback, with deterministic rules as the fast path.

Keep the shared LLM adapter in `minx_mcp/core/` for now, but treat that as temporary. Defer moving the shared adapter out of `core/` into a domain-neutral module until Meals proves the shared pattern.

## Event Model

Meals event registration is explicit and enforced:

- Every emitted Meals event type must have a payload model.
- Unknown event types raise through the existing event validation path.
- Payloads use domain-local typed fields.
- Timeline summaries handle `meal.logged` and `nutrition.day_updated`.
- Event payloads avoid embedding full recipe or pantry records when a stable entity reference is enough.
- Meals declares its event payloads in a Meals-owned module or mapping; the shared event emitter composes the registry.
- Finance event declarations should move toward the same shape so `minx_mcp/core/events.py` is not the long-term owner of every domain event string.

Recommended payload sketches:

```python
class MealLoggedPayload(EventPayload):
    meal_id: int
    meal_kind: str
    food_count: int
    protein_grams: float | None = None
    calories: int | None = None


class NutritionDayUpdatedPayload(EventPayload):
    date: str
    meal_count: int
    protein_grams: float | None = None
    calories: int | None = None
```

## Core Integration

Add a Meals read API interface analogous to Finance's read API. Core builds a daily snapshot from multiple domain contributions:

- timeline from platform events
- finance spending and open loops from Finance
- goal progress from Goals and Finance reads
- nutrition from Meals
- deterministic signals from Core detectors

Keep `NutritionSnapshot` discrete and explicit. This may later evolve into a broader domain contribution container, but do not introduce that abstraction before Meals exists and proves the need.

Keep nutrition detectors and meal signals deterministic in Core. Leave narrative, coaching, and follow-up prioritization to the harness.

Keep using the current shared LLM adapter in `minx_mcp/core/` for now, with narrow call boundaries so Finance and Meals can both use it. The domain-neutral move remains deferred.

The implementation should avoid two traps:

- Do not keep stretching a Finance-first `DailySnapshot` shape by adding unrelated domain fields forever.
- Do not jump straight to a generic `DomainAdapter`.

The intended middle step is small and concrete: Core composes known domain contributions from Finance, Goals, and Meals for this slice. After Meals exists, extract only the common composition mechanics that both domains actually use.

## Future Registry Direction

After Meals exists, the likely platform shape is a set of small registries that Core composes:

- Domain event registry: each domain declares event payload models and summaries; the shared emitter enforces known event types.
- Domain read-model contribution registry: each domain exposes read-only snapshot builders for Core to assemble.
- Domain detector contribution registry: Core owns deterministic signal execution, but detector groups can be registered by domain instead of added to one growing list.

This future shape should not become a giant router or a mega-server. It is a way to keep explicit domain boundaries while reducing central switch statements after the second domain proves the pattern.

## Recipe Recommendation Behavior

Recommendation is deterministic and inspectable.

The default recommendation set prioritizes recipes the user can cook now. Recipes that require only explicit substitutions come after fully covered recipes. Recipes that require shopping are not part of the default recommendation result unless there are no better matches or the user explicitly asks for options that require groceries.

### Availability Classes

Classify every candidate recipe into one of these classes:

1. `make_now`
2. `make_with_substitutions`
3. `needs_shopping`
4. `excluded`

Definitions:

- `make_now`: every required ingredient is available in pantry at sufficient quantity.
- `make_with_substitutions`: every required ingredient is either available or satisfied by an explicit substitution.
- `needs_shopping`: one or more required ingredients are missing and no substitution covers them.
- `excluded`: the recipe cannot be made from pantry, substitutions, or a reasonable shopping fallback, or it fails basic data quality rules.

Optional ingredients do not determine availability class and do not appear in shopping list diffs.

### Exact Ranking Logic

Sort primarily by availability class in this order:

1. `make_now`
2. `make_with_substitutions`
3. `needs_shopping`
4. `excluded`

Within a class, sort by:

1. `expiring_ingredient_hits` descending
2. `low_stock_ingredient_hits` descending
3. `pantry_coverage_ratio` descending
4. `missing_required_count` ascending
5. `substitution_count` ascending
6. `recipe_title` ascending

The sort key should be implemented literally and covered with deterministic tests.

### Default Recommendation Output Rules

- Return `make_now` recipes first.
- Return `make_with_substitutions` recipes second.
- Show `needs_shopping` recipes only if the user explicitly asks for "what should I cook if I buy groceries" or if there are no better matches.
- Do not generate shopping lists for `make_now` or `make_with_substitutions` recipes.
- Do not add missing ingredients to a shopping list unless the user selects a recipe that actually needs them.

### Recommendation Response Shape

The response should give the harness enough structured data to present the answer without reimplementing Meals logic:

```python
{
    "recommendations": [
        {
            "recipe_id": 42,
            "recipe_title": "Chickpea Pasta",
            "availability_class": "make_now",
            "pantry_coverage_ratio": 1.0,
            "expiring_ingredient_hits": 1,
            "low_stock_ingredient_hits": 0,
            "missing_required_count": 0,
            "substitution_count": 0,
            "matched_ingredients": ["pasta", "chickpeas", "spinach"],
            "substitutions": [],
            "missing_required_ingredients": [],
            "reasons": ["uses spinach expiring soon"],
            "recipe": {
                "vault_path": "Recipes/Chickpea Pasta.md",
                "image_ref": "Assets/chickpea-pasta.jpg",
                "tags": ["dinner"],
                "source_url": "https://example.com/chickpea-pasta"
            }
        }
    ],
    "included_classes": ["make_now", "make_with_substitutions"],
    "shopping_lists_generated": []
}
```

`shopping_lists_generated` should be empty for the default recommendation path.

## Shopping List Rules

Shopping list generation is Phase 3. The first implementation slice should not generate shopping lists as a side effect of recommendation.

When Phase 3 lands:

- A shopping list is a fallback, not the default.
- Generate a shopping list only when a chosen recipe has missing required ingredients that are not covered by pantry or substitutions.
- Include only missing required ingredients, not optional ingredients.
- Exclude ingredients already present in pantry at sufficient quantity.
- Exclude ingredients covered by substitutions.
- If a recipe has multiple substitutions, prefer the first valid substitution choice unless a later spec defines a better priority rule.
- Write shopping lists as generated artifacts, preferably in Obsidian, while keeping pantry truth in Meals SQLite tables.

## Pantry And Inventory Rules

Pantry and inventory records must track:

- quantity
- unit
- item identity
- normalized ingredient identity
- expiration date
- low-stock threshold where known

Inventory must support low-stock awareness for frequently used ingredients. Expiring items should influence ranking even when they are not strictly required by a recipe, provided the match is deterministic and explainable.

Pantry should be queryable for exact match and normalized ingredient match. Normalization should be deterministic and test-covered before any LLM-assisted cleanup is allowed to persist.

## Recipe Data Rules

Recipe notes should support:

- ingredients
- instructions
- nutrition
- tags
- optional image or asset metadata
- optional substitutions
- prep time
- cook time
- servings
- source metadata

Recipe ingredients should support required versus optional behavior. Recipe ingredients should also support explicit substitution candidates.

If a recipe lacks picture metadata, the system should still work normally. If a recipe has picture metadata, the harness should surface it.

## Phase 2: Recipe Recommendation

Recipe recommendation adds the deterministic path needed for "what should I cook today?"

Required behavior:

- Index Obsidian recipes into Meals.
- Normalize recipe ingredients for pantry matching.
- Add substitution matching.
- Add expiring-item awareness.
- Add low-stock ingredient awareness.
- Return ranked recipe candidates with availability class and reasons.
- Verify "recommend only what I can actually cook" behavior with tests.

This phase is part of the first implementation slice.

## Phase 3: Shopping List Generation (Deferred)

Shopping list generation is the next phase after foundation and recommendation. It is documented here to keep the recipe and pantry model honest, but it is not part of the first implementation slice.

Required behavior:

- Generate a shopping list only from the missing required ingredients of a chosen recipe.
- Write shopping lists as generated artifacts, preferably in Obsidian.
- Support diffing against pantry quantities, not just presence or absence.
- Verify only necessary items appear.
- Verify substituted and optional items do not leak into the shopping list.

### Shopping list markdown template

Because shopping lists are deterministic SQL-backed renders (not LLM-authored) written to Obsidian on a recurring basis, they fit the same template pattern as the finance weekly/monthly reports. Ship a scaffold alongside the meals package:

- **Location:** `minx_mcp/meals/templates/shopping-list.md`, wired in `pyproject.toml` via `"minx_mcp.meals.templates" = ["*.md"]` — same pattern as `minx_mcp/finance/templates/` and `minx_mcp/schema/migrations/`. Cover in the wheel-packing test at `tests/test_db.py::test_built_wheel_includes_packaged_resources`.
- **Fill semantics:** `string.Template` `${placeholder}` (identical to finance report renderers), populated from the generated-artifact SQL (`meals_shopping_lists` + `meals_shopping_list_items`).
- **Structure (proposed):** YAML frontmatter with `type: minx-shopping-list` + `generated_at` + `source_recipe` so the vault scanner (Slice 6c) can index it; a fixed section layout such as `## Missing Ingredients`, `## Covered by Pantry`, `## Covered by Substitutions` so later queries (and any human glance) can parse it without guessing.

The loader should mirror `minx_mcp/finance/report_builders.py`:

```python
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
template = Template((TEMPLATE_DIR / "shopping-list.md").read_text(encoding="utf-8"))
```

This phase should not be pulled into the first implementation plan unless the user explicitly expands scope.

## Phase 4: Rich Presentation (Deferred)

Richer presentation is metadata support, not business logic. It is not part of the first implementation slice except for preserving recipe metadata fields that would be expensive to retrofit later.

Required behavior:

- Surface recipe images or asset paths in recipe detail responses.
- Surface useful metadata like prep time, cook time, servings, tags, and source.
- Add richer recipe card outputs for harness presentation.

The harness owns final presentation and narration.

## Tests

The first implementation slice should include tests for:

- Meals server tools and validation.
- Meals event payload registration and unknown-event failure.
- Meal logging and nutrition day update event emission.
- Core read-model assembly with a discrete `NutritionSnapshot`.
- Core detectors for low protein and skipped-meal patterns.
- Recipe note indexing from an isolated temp vault.
- Pantry exact and normalized ingredient matching.
- Availability classification across `make_now`, `make_with_substitutions`, `needs_shopping`, and `excluded`.
- Ranking order, including expiring items, low-stock hits, coverage ratio, missing required count, substitution count, and title tie-break.
- Default recommendation output excluding shopping-list side effects.

Phase 3 should add tests for:

- shopping list generation from missing required ingredients only
- quantity-aware pantry diffs
- substituted ingredients excluded from shopping lists
- optional ingredients excluded from shopping lists

Reuse the isolated temp-vault and temp-db test style where it fits.

### In-Memory MCP Testing Pattern (informed by FastMCP)

Meals server tool tests should use `FastMCP.call_tool()` for in-memory testing instead of the subprocess stdio pattern used in the Core e2e test. The MCP SDK (v1.27.0+) supports direct tool invocation:

```python
server = create_meals_server(config)
result = await server.call_tool("meal_log", {"meal_kind": "lunch", ...})
assert result["success"] is True
```

This eliminates subprocess overhead, makes tests faster and more deterministic, and removes the need for environment variable plumbing. Reserve the subprocess stdio test for one smoke test per server; use in-memory `call_tool` for all other tool-level tests.

The existing Core stdio e2e test (`test_core_mcp_stdio.py`) should remain as-is — it validates the real transport path. New Meals tool tests should default to in-memory.

## Acceptance Criteria

For the first implementation slice:

- The launcher can start `minx-core`, `minx-finance`, and `minx-meals` without merging their domain logic.
- `minx-meals` is independently runnable.
- The harness can ask, "what should I cook today?"
- The system returns recipes the user can actually make now or with explicit substitutions.
- Recipes using expiring ingredients or low-stock ingredients rank ahead of equivalent recipes that do not.
- A recipe that needs shopping does not trigger a shopping list in the default recommendation path.
- Meals events are emitted and recorded safely.
- Core daily snapshots include nutrition context.
- Tests cover the recommendation path, substitutions, pantry matching, event validation, and Core nutrition read-model assembly.
- Old SousChef behaviors are reused only where they clearly improve the new system.

For Phase 3:

- A shopping list is generated only after the user selects or explicitly requests a recipe that needs shopping.
- A shopping list contains only truly missing required ingredients.
- Substituted and optional ingredients do not appear in generated shopping lists.

## Deferred Items

- Full SousChef parity.
- HEB automation.
- Browser-driven shopping checkout.
- Training MCP integration.
- LLM-first meal generation.
- OCR or image-based meal logging.
- A generalized domain adapter abstraction before Meals exists.
- Moving the shared LLM adapter out of `core/` into a domain-neutral module before Meals proves the pattern.
- Goals expansion into Meals unless later product decisions call for it.

## Risks And Open Questions

These questions should be resolved during implementation planning, not by widening this design:

- Whether recipe storage should be indexed only, or also normalized into a Meals recipe table.
- Whether pantry should be canonical in SQLite only, or also projected into Obsidian for human editing.
- Whether `meal.plan_updated` belongs in a later planning slice.
- Whether `NutritionSnapshot` should stay as one field on `DailySnapshot` or become the first step toward a broader domain contribution container after Meals exists.
- Whether substitutions should remain static per recipe, ingredient-class based, or learned over time.

## Implementation Planning Notes

Do not broaden this into Training, HEB automation, browser checkout, or full SousChef parity.

The next plan should be foundation plus recommendation:

1. Add the launcher boundary while keeping each MCP server independently runnable.
2. Add Meals schema, service, server, read API, events, and recipe indexing.
3. Add Core nutrition snapshot assembly and deterministic nutrition detectors.
4. Add pantry-aware deterministic recipe classification and ranking.
5. Add tests that pin default recommendation behavior and prevent shopping list side effects.

Shopping list generation should be the clear next phase after this first slice.

## External Pattern References

Patterns from the following repos informed specific sections of this design:

- **PANTS** (recipe composition) — the Ingredient → Recipe → LoggedMeal entity separation in the Data Model Guidance section. Source of the "nutritional truth per normalized unit" concept and recursive composition model.
- **RoXsaita/NumbyAI-Public** (rule-first categorization) — the rule-first + LLM-fallback pattern in the Parsing and LLM Policy section. Source of the "deterministic rules as stored data" and "promote successful LLM resolutions to rules" concepts.
- **jlowin/fastmcp** (in-memory testing) — the `FastMCP.call_tool()` testing pattern in the Tests section. Source of the "stop vibe-testing your MCP server" principle.
- **pyeventsourcing/eventsourcing** (composable event registry) — the per-domain event payload declaration pattern in the Platform Primitives section. Source of the composable registry approach.

Patterns deferred to later slices:
- **ErikBjare/quantifiedme** (timeline normalization) → Slice 4, when a third domain makes timeline merging non-trivial.
- **traceloop/openllmetry** (OTel tracing) → Slice 6, when multi-server observability becomes essential.
- **mattbishop/sql-event-store** (CTE replay views) → Slice 6, for review reproducibility.
