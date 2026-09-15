from contextlib import redirect_stdout
from datetime import date
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "bin" / "mealplan"
loader = importlib.machinery.SourceFileLoader("custos_mealplan_cli", str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
mealplan = importlib.util.module_from_spec(spec)
loader.exec_module(mealplan)


class MealQueryTests(unittest.TestCase):
    def test_scope_uses_current_week_and_explicit_historical_dates(self):
        today = date(2026, 9, 14)
        self.assertEqual(mealplan.meals_scope(today=today), ("2026-W38", None))
        self.assertEqual(mealplan.meals_scope("tomorrow", today=today), ("2026-W38", "2026-09-15"))
        self.assertEqual(mealplan.meals_scope("2025-12-31", today=today), ("2026-W01", "2025-12-31"))
        self.assertEqual(mealplan.meals_scope("2026-W04", today=today), ("2026-W04", None))

    def test_tomorrow_prints_only_that_meal_and_canonical_link(self):
        calendar = {"start": "2026-09-15", "end": "2026-09-15", "count": 1, "meals": [
            {"date": "2026-09-15", "title": "Cauliflower Curry", "slug": "cauliflower-curry",
             "url": "https://recipes.ha1.io/g/home/r/cauliflower-curry"},
        ]}
        out = io.StringIO()
        with patch.object(mealplan, "meals_scope", return_value=("2026-W38", "2026-09-15")), \
             patch.object(mealplan, "call", return_value=calendar) as call, \
             patch.object(sys, "argv", ["mealplan", "meals", "tomorrow"]), redirect_stdout(out):
            mealplan.main()
        call.assert_called_once_with("GET", "/meals", params={"start": "2026-09-15", "end": "2026-09-15"})
        self.assertIn("Cauliflower Curry", out.getvalue())
        self.assertIn("https://recipes.ha1.io/g/home/r/cauliflower-curry", out.getvalue())

    def test_week_query_uses_monday_through_sunday(self):
        self.assertEqual(mealplan.meals_range("2026-W38"), ("2026-09-14", "2026-09-20"))


if __name__ == "__main__":
    unittest.main()
