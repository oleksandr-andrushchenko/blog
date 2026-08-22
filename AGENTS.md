# Repository agent instructions

- Keep `shared` limited to genuinely shared domain models, persistence primitives, validation, and helpers used by both Lambdas.
- Before adding or keeping a helper in `shared`, search its callers across `api-lambda` and `web-lambda`. If it is used by only one Lambda, place it in that Lambda's module; web-only helpers belong in `web-lambda/web_utils.py`.
- Lambda-specific orchestration, page composition, and presentation-oriented query helpers must remain in the owning Lambda.
- Do not rely on wildcard imports or accidental re-exports for new dependencies; import names explicitly and remove imports that are not referenced.
- Prefer native Bootstrap classes and components for UI changes; avoid modifying `static/styles.css` unless the requirement cannot be implemented cleanly with Bootstrap.
