from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime

import pytest

import minx_mcp.meals.service as meals_service_module
from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import query_events
from minx_mcp.db import get_connection
from minx_mcp.meals.service import MealsService, ReconcileRecipesResult
from minx_mcp.preferences import set_preference


def test_log_meal_emits_event(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        entry = svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="Grilled chicken salad",
            food_items=[{"name": "rice"}, {"name": "chicken"}],
            protein_grams=40.0,
            calories=700,
        )

    assert entry.id > 0
    assert entry.meal_kind == "lunch"
    conn = get_connection(db_path)
    try:
        events = query_events(conn, domain="meals", event_type="meal.logged")
    finally:
        conn.close()
    assert len(events) == 1
    assert events[0].payload["food_count"] == 2
    assert events[0].payload["protein_grams"] == 40.0


def test_log_meal_emits_nutrition_day_updated_after_commit(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        set_preference(svc.conn, "core", "timezone", "UTC")
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="Salad",
            protein_grams=40.0,
            calories=700,
        )

    conn = get_connection(db_path)
    try:
        day_events = query_events(conn, domain="meals", event_type="nutrition.day_updated")
    finally:
        conn.close()

    assert len(day_events) == 1
    assert day_events[0].payload["date"] == "2026-04-12"
    assert day_events[0].payload["meal_count"] == 1
    assert day_events[0].payload["protein_grams"] == 40.0
    assert day_events[0].payload["calories"] == 700


def test_log_meal_rolls_back_if_nutrition_day_event_fails_to_emit(db_path, monkeypatch) -> None:
    original_emit = meals_service_module.emit_event

    def emit_shim(*args, **kwargs):
        if kwargs.get("event_type") == "nutrition.day_updated":
            raise RuntimeError("simulated bus failure")
        return original_emit(*args, **kwargs)

    monkeypatch.setattr(meals_service_module, "emit_event", emit_shim)
    svc = MealsService(db_path)
    with pytest.raises(RuntimeError, match="simulated bus failure"), svc:
        set_preference(svc.conn, "core", "timezone", "UTC")
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="should roll back",
            protein_grams=10.0,
            calories=100,
        )

    conn = get_connection(db_path)
    try:
        meal_rows = conn.execute("SELECT COUNT(*) FROM meals_meal_entries").fetchone()[0]
        logged = query_events(conn, domain="meals", event_type="meal.logged")
        day = query_events(conn, domain="meals", event_type="nutrition.day_updated")
    finally:
        conn.close()
    assert meal_rows == 0
    assert len(logged) == 0
    assert len(day) == 0


def test_log_meal_commits_meal_and_day_event_atomically(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        set_preference(svc.conn, "core", "timezone", "UTC")
        entry = svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="Salad",
            protein_grams=30.0,
            calories=500,
        )

    assert entry.id > 0
    conn = get_connection(db_path)
    try:
        persisted = conn.execute(
            "SELECT COUNT(*) FROM meals_meal_entries WHERE id = ?",
            (entry.id,),
        ).fetchone()[0]
        logged = query_events(conn, domain="meals", event_type="meal.logged")
        day = query_events(conn, domain="meals", event_type="nutrition.day_updated")
    finally:
        conn.close()
    assert persisted == 1
    assert len(logged) == 1
    assert len(day) == 1


def test_log_meal_day_event_reflects_current_meal(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        set_preference(svc.conn, "core", "timezone", "UTC")
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="Bowl",
            protein_grams=42.5,
            calories=620,
            carbs_grams=45.0,
            fat_grams=18.0,
        )

    conn = get_connection(db_path)
    try:
        day_events = query_events(conn, domain="meals", event_type="nutrition.day_updated")
    finally:
        conn.close()
    assert len(day_events) == 1
    payload = day_events[0].payload
    assert payload["meal_count"] == 1
    assert payload["protein_grams"] == 42.5
    assert payload["calories"] == 620


def test_nutrition_day_updated_aggregates_multiple_meals_in_local_day(db_path) -> None:
    """Two meals logged on the same local day must roll up into one updated event per log.

    The final event must reflect the running total (meal_count == 2, summed macros),
    not just the delta from the latest meal. Anchors the documented aggregation contract.
    """
    svc = MealsService(db_path)
    with svc:
        set_preference(svc.conn, "core", "timezone", "UTC")
        svc.log_meal(
            occurred_at="2026-04-12T08:00:00Z",
            meal_kind="breakfast",
            summary="Eggs",
            protein_grams=25.0,
            calories=400,
        )
        svc.log_meal(
            occurred_at="2026-04-12T19:30:00Z",
            meal_kind="dinner",
            summary="Salmon",
            protein_grams=35.5,
            calories=650,
        )

    conn = get_connection(db_path)
    try:
        day_events = query_events(conn, domain="meals", event_type="nutrition.day_updated")
    finally:
        conn.close()

    assert len(day_events) == 2
    latest = day_events[-1]
    assert latest.payload["date"] == "2026-04-12"
    assert latest.payload["meal_count"] == 2
    assert latest.payload["protein_grams"] == 60.5
    assert latest.payload["calories"] == 1050


def test_log_meal_validates_kind(db_path) -> None:
    svc = MealsService(db_path)
    with pytest.raises(InvalidInputError, match="invalid"), svc:
        svc.log_meal(occurred_at="2026-04-12T12:00:00Z", meal_kind="invalid")


def test_log_meal_rolls_back_when_event_emission_fails(db_path, monkeypatch) -> None:
    svc = MealsService(db_path)
    monkeypatch.setattr(meals_service_module, "emit_event", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError), svc:
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="event failure",
        )
    conn = get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM meals_meal_entries").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_pantry_crud(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        item = svc.add_pantry_item(display_name="Eggs", quantity=12, unit="count")
        updated = svc.update_pantry_item(item.id, quantity=6)
        items = svc.list_pantry_items()
        svc.remove_pantry_item(item.id)
        with pytest.raises(NotFoundError, match=str(item.id)):
            svc.get_pantry_item(item.id)

    assert item.normalized_name == "egg"
    assert updated.quantity == 6
    assert len(items) == 1


def test_index_recipe_from_vault_updates_on_content_change(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        first = svc.index_recipe("Recipes/Soup.md")
        note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n- 2 carrots\n")
        second = svc.index_recipe("Recipes/Soup.md")

    assert second.id == first.id
    assert len(second.ingredients) == 2


def test_index_recipe_canonicalizes_vault_relative_path(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        first = svc.index_recipe(str(note))
        second = svc.index_recipe("Recipes/Soup.md")
        count = svc.conn.execute("SELECT COUNT(*) FROM meals_recipes").fetchone()[0]

    assert second.id == first.id
    assert second.vault_path == "Recipes/Soup.md"
    assert count == 1


def test_index_recipe_rejects_paths_outside_vault(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "Outside.md"
    outside.write_text("---\ntitle: Outside\n---\n## Ingredients\n- 1 egg\n")
    svc = MealsService(db_path, vault_root=vault)

    with pytest.raises(InvalidInputError, match=r"inside vault_root"), svc:
        svc.index_recipe("../Outside.md")


def test_scan_vault_recipes_rejects_directories_outside_vault(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Outside.md").write_text("---\ntitle: Outside\n---\n## Ingredients\n- 1 egg\n")
    svc = MealsService(db_path, vault_root=vault)

    with pytest.raises(InvalidInputError, match=r"inside vault_root"), svc:
        svc.scan_vault_recipes(str(outside))


def test_set_nutrition_profile_persists_calculated_targets(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        result = svc.set_nutrition_profile(
            sex="male",
            age_years=30,
            height_cm=180.0,
            weight_kg=80.0,
            activity_level="moderately_active",
            goal="maintenance",
            calorie_deficit_kcal=400,
            protein_g_per_kg=2.0,
            fat_g_per_kg=0.77,
        )
        profile = svc.get_nutrition_profile()

    assert result.targets.bmr_kcal == 1780
    assert result.targets.tdee_kcal == 2759
    assert result.targets.calorie_target_kcal == 2359
    assert result.targets.protein_target_grams == 160
    assert result.targets.fat_target_grams == 62
    assert result.targets.carbs_target_grams == 290
    assert profile is not None
    assert profile.activity_level == "moderately_active"


def test_set_nutrition_profile_validates_inputs(db_path) -> None:
    svc = MealsService(db_path)
    with pytest.raises(InvalidInputError, match=r"age_years must be a positive integer"), svc:
        svc.set_nutrition_profile(
            sex="male",
            age_years=0,
            height_cm=180.0,
            weight_kg=80.0,
            activity_level="moderately_active",
        )


def test_list_meals_uses_timezone_local_day_boundaries(db_path) -> None:
    svc = MealsService(db_path)
    with svc:
        set_preference(svc.conn, "core", "timezone", "America/Chicago")
        svc.log_meal(
            occurred_at="2026-04-13T04:30:00Z",
            meal_kind="snack",
            summary="late prior local day",
        )
        svc.log_meal(
            occurred_at="2026-04-13T05:30:00Z",
            meal_kind="breakfast",
            summary="same local day after midnight",
        )
        meals = svc.list_meals("2026-04-13")

    assert [meal.summary for meal in meals] == ["same local day after midnight"]


def test_set_nutrition_profile_goal_cut_lowers_calories_vs_maintenance(db_path) -> None:
    svc = MealsService(db_path)
    base = dict(
        sex="male",
        age_years=30,
        height_cm=180.0,
        weight_kg=80.0,
        activity_level="moderately_active",
        calorie_deficit_kcal=400,
        protein_g_per_kg=2.0,
        fat_g_per_kg=0.77,
    )
    with svc:
        maintenance = svc.set_nutrition_profile(goal="maintenance", **base)
        cut = svc.set_nutrition_profile(goal="cut", **base)

    assert cut.targets.calorie_target_kcal < maintenance.targets.calorie_target_kcal


def test_index_recipe_rolls_back_entirely_on_ingredient_integrity_error(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n- 2 carrots\n"
    )
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        conn = svc.conn
        conn.execute(
            """
            CREATE TEMP TRIGGER meals_test_abort_second_ingredient
            BEFORE INSERT ON meals_recipe_ingredients
            WHEN (
                SELECT COUNT(*) FROM meals_recipe_ingredients r
                WHERE r.recipe_id = NEW.recipe_id
            ) >= 1
            BEGIN
                SELECT RAISE(ABORT, 'forced second ingredient failure');
            END
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced second ingredient failure"):
            svc.index_recipe("Recipes/Soup.md")

        recipe_count = conn.execute("SELECT COUNT(*) FROM meals_recipes").fetchone()[0]
        ingredient_count = conn.execute("SELECT COUNT(*) FROM meals_recipe_ingredients").fetchone()[
            0
        ]

    assert recipe_count == 0
    assert ingredient_count == 0


def test_index_recipe_persists_nutrition_summary_from_frontmatter(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Tagged.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        '---\ntitle: Tagged\nnutrition: {"calories": 512, "protein_grams": 44}\n---\n'
        "## Ingredients\n- 1 egg\n"
    )
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        recipe = svc.index_recipe("Recipes/Tagged.md")

    assert recipe.nutrition_summary == {"calories": 512, "protein_grams": 44}


def _parse_vault_synced_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_index_recipe_stamps_vault_synced_at(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        recipe = svc.index_recipe("Recipes/Soup.md")
        row = svc.conn.execute(
            "SELECT vault_synced_at FROM meals_recipes WHERE id = ?",
            (recipe.id,),
        ).fetchone()

    assert row is not None
    assert row["vault_synced_at"] is not None
    ts = _parse_vault_synced_at(str(row["vault_synced_at"]))
    assert ts.tzinfo == UTC


def test_index_recipe_refreshes_vault_synced_at_on_reindex(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        first = svc.index_recipe("Recipes/Soup.md")
        t0 = _parse_vault_synced_at(
            str(
                svc.conn.execute(
                    "SELECT vault_synced_at FROM meals_recipes WHERE id = ?",
                    (first.id,),
                ).fetchone()["vault_synced_at"]
            )
        )
        time.sleep(1)
        svc.index_recipe("Recipes/Soup.md")
        t1 = _parse_vault_synced_at(
            str(
                svc.conn.execute(
                    "SELECT vault_synced_at FROM meals_recipes WHERE id = ?",
                    (first.id,),
                ).fetchone()["vault_synced_at"]
            )
        )

    assert t1 > t0


def test_reconcile_vault_recipes_nullifies_missing_vault_path(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        svc.conn.execute(
            """
            INSERT INTO meals_recipes (vault_path, title, normalized_title, content_hash)
            VALUES ('nope/Missing.md', 'Ghost', 'ghost', 'deadbeef')
            """
        )
        svc.conn.commit()
        rid = int(svc.conn.execute("SELECT id FROM meals_recipes").fetchone()["id"])
        result = svc.reconcile_vault_recipes()
        row = svc.conn.execute("SELECT vault_path FROM meals_recipes WHERE id = ?", (rid,)).fetchone()

    assert isinstance(result, ReconcileRecipesResult)
    assert result.checked == 1
    assert result.orphaned == 1
    assert result.orphaned_recipe_ids == [rid]
    assert row["vault_path"] is None


def test_reconcile_vault_recipes_preserves_existing_files(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        recipe = svc.index_recipe("Recipes/Soup.md")
        result = svc.reconcile_vault_recipes()
        row = svc.conn.execute(
            "SELECT vault_path, vault_synced_at FROM meals_recipes WHERE id = ?",
            (recipe.id,),
        ).fetchone()

    assert result.checked == 1
    assert result.orphaned == 0
    assert result.orphaned_recipe_ids == []
    assert row["vault_path"] == "Recipes/Soup.md"
    assert row["vault_synced_at"] is not None


def test_reconcile_vault_recipes_is_idempotent(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        svc.conn.execute(
            """
            INSERT INTO meals_recipes (vault_path, title, normalized_title, content_hash)
            VALUES ('gone/Stew.md', 'Stew', 'stew', 'abc123')
            """
        )
        svc.conn.commit()
        first = svc.reconcile_vault_recipes()
        second = svc.reconcile_vault_recipes()

    assert first.orphaned == 1
    assert second.orphaned == 0
    assert second.checked == 0


def test_reconcile_vault_recipes_emits_recipe_orphaned_event(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        svc.conn.execute(
            """
            INSERT INTO meals_recipes (vault_path, title, normalized_title, content_hash)
            VALUES ('orphan/Pasta.md', 'Pasta', 'pasta', 'pasta1')
            """
        )
        svc.conn.commit()
        rid = int(svc.conn.execute("SELECT id FROM meals_recipes").fetchone()["id"])
        svc.reconcile_vault_recipes()

    conn = get_connection(db_path)
    try:
        events = query_events(conn, domain="meals", event_type="meals.recipe_orphaned")
    finally:
        conn.close()

    assert len(events) == 1
    payload = events[0].payload
    assert payload["recipe_id"] == rid
    assert payload["slug"] == "pasta"
    assert payload["previous_vault_path"] == "orphan/Pasta.md"
    assert payload["reason"] == "vault_file_missing"


def test_reconcile_vault_recipes_preserves_recipe_id_for_meal_logs(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        recipe = svc.index_recipe("Recipes/Soup.md")
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="dinner",
            food_items=[{"recipe_id": recipe.id, "name": "Soup"}],
        )
        note.unlink()
        svc.reconcile_vault_recipes()
        meal_row = svc.conn.execute(
            "SELECT food_items_json FROM meals_meal_entries ORDER BY id DESC LIMIT 1"
        ).fetchone()

    items = json.loads(str(meal_row["food_items_json"]))
    assert items == [{"recipe_id": recipe.id, "name": "Soup"}]


def test_scan_vault_recipes_calls_reconcile_first(db_path, tmp_path) -> None:
    vault = tmp_path / "vault"
    (vault / "Recipes").mkdir(parents=True)
    svc = MealsService(db_path, vault_root=vault)

    with svc:
        svc.conn.execute(
            """
            INSERT INTO meals_recipes (vault_path, title, normalized_title, content_hash)
            VALUES ('Recipes/Zombie.md', 'Zombie', 'zombie', 'z1')
            """
        )
        svc.conn.commit()
        svc.scan_vault_recipes("Recipes")
        row = svc.conn.execute(
            "SELECT vault_path FROM meals_recipes WHERE normalized_title = 'zombie'"
        ).fetchone()

    assert row["vault_path"] is None
