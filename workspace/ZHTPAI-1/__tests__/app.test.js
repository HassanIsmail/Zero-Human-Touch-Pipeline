The file write requires your permission. Please approve the write operation, and once it's done the test file will be saved to `__tests__/app.test.js`.

The file covers all 5 acceptance criteria plus the additional requirements, organized into 8 `describe` blocks:

1. **AC1 — Add todo** (11 tests): form submission, multiple adds, input clearing, empty/whitespace rejection, trimming, DOM structure, button type
2. **AC2 — Mark complete** (9 tests): `done` class toggling, aria-label changes, SVG checkmark, bidirectional toggle, isolation from other todos
3. **AC3 — Delete todo** (6 tests): removal, non-interference with siblings, empty-state restore, aria-label, deleting a completed todo
4. **AC4 — Remaining count** (12 tests): hidden/visible state, singular/plural text, decrements on complete and delete, increments on un-complete
5. **AC5 — localStorage persistence** (13 tests): save on add/toggle/delete, reload restores todos and done state and count, corrupted JSON fallback, null fallback
6. **No console errors** (5 tests): `console.error` spy across load, add, toggle, delete, and reload-with-data
7. **Mobile usability** (8 tests): viewport meta, `width=device-width`, `initial-scale`, `maxlength`, `autocomplete=off`, all elements present, full workflow
8. **Pure function API** (20 tests): unit tests for every exported function — `addTodo`, `toggleTodo`, `deleteTodo`, `countRemaining`, `createTodo`, `saveTodos`, `loadTodos`