# Style Module Map

The UI style system is split by responsibility and loaded in this order via `src/styles/app.css`:

1. `modules/tokens-base.css`
2. `modules/layout-shell.css`
3. `modules/components-core.css`
4. `modules/page-pipeline.css`
5. `modules/page-insights.css`
6. `modules/page-library-review.css`
7. `modules/theme-clay.css`

`theme-clay.css` is intentionally last so it can override base/page styles without `!important`.

## Tailwind status

This frontend currently uses custom CSS classes (no Tailwind runtime/config in this package).
If Tailwind is reintroduced later, keep it in an isolated entry file to avoid accidental cascade conflicts.
