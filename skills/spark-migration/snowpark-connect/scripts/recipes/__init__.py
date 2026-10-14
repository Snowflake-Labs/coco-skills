"""Deterministic LibCST recipes for the snowpark-connect migration skill.

Each subdirectory contains a single ``recipe.py`` that exposes an
``apply(source: str, ...) -> RecipeResult`` entry point. Recipes apply
byte-level syntax-tree rewrites that the LLM fixer agent and the existing
``fallback_transform.py`` cannot do reliably (e.g. preserving
``SparkSession.builder.config(...)`` calls when rewriting the builder chain).

See ``_common.py`` for the shared LibCST scaffolding and ``_recipe_base.py``
for the (optional) sqlite-backed edit log. Recipes are auto-discovered by
``scripts/preprocess_recipes.py`` -- any directory under this package that
does not start with ``_`` and contains a ``recipe.py`` is loaded and run.
"""
