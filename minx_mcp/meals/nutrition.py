from __future__ import annotations

from dataclasses import dataclass

ACTIVITY_MULTIPLIERS: dict[str, float] = {
    "sedentary": 1.2,
    "lightly_active": 1.375,
    "moderately_active": 1.55,
    "very_active": 1.725,
}

MIN_CALORIE_FLOOR_KCAL = 1200

# Goal multipliers apply to TDEE before subtracting ``calorie_deficit_kcal``:
#   cut (-15% vs TDEE): 0.85
#   maintenance (0%): 1.0
#   bulk (+10% vs TDEE): 1.10
GOAL_CALORIE_MULTIPLIERS: dict[str, float] = {
    "cut": 0.85,
    "maintenance": 1.0,
    "bulk": 1.10,
}

SEX_BMR_OFFSETS: dict[str, int] = {
    "male": 5,
    "female": -161,
    # Midpoint between the male/female constants for a neutral fallback.
    "other": -78,
}


@dataclass(frozen=True)
class NutritionTargetsCalculated:
    bmr_kcal: int
    tdee_kcal: int
    calorie_target_kcal: int
    protein_target_grams: int
    fat_target_grams: int
    carbs_target_grams: int


def calculate_nutrition_targets(
    *,
    sex: str,
    age_years: int,
    height_cm: float,
    weight_kg: float,
    activity_level: str,
    goal: str,
    calorie_deficit_kcal: int,
    protein_g_per_kg: float,
    fat_g_per_kg: float,
) -> NutritionTargetsCalculated:
    bmr = round(10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years + float(SEX_BMR_OFFSETS[sex]))
    tdee = round(float(bmr) * ACTIVITY_MULTIPLIERS[activity_level])
    multiplier = GOAL_CALORIE_MULTIPLIERS[goal]
    adjusted_tdee = round(float(tdee) * multiplier)
    calorie_target = max(adjusted_tdee - calorie_deficit_kcal, MIN_CALORIE_FLOOR_KCAL)
    protein_grams = max(round(weight_kg * protein_g_per_kg), 0)
    fat_grams = max(round(weight_kg * fat_g_per_kg), 0)
    carbs_calories = max(calorie_target - (protein_grams * 4) - (fat_grams * 9), 0)
    carbs_grams = int(carbs_calories / 4)
    return NutritionTargetsCalculated(
        bmr_kcal=bmr,
        tdee_kcal=tdee,
        calorie_target_kcal=calorie_target,
        protein_target_grams=protein_grams,
        fat_target_grams=fat_grams,
        carbs_target_grams=carbs_grams,
    )
