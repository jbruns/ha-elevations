# Displays are detachable

A Display is an output surface, not a dependency. No blueprint, package or other Display reads state that only exists for a Display. Removing Our Home or the Wallboard must not break an automation or another context.

A Display may consume another context's role entity only when the role's meaning is exactly that context's term. For example, a Wallboard hazards section may use Safety's **Immediate Hazards**, and an open-doors section may use Climate's **Exterior Doors**. If the meaning is only for presentation, the Display owns a Display-local role in its own package instead of stretching another context's language.

## Consequences

- Displays can be replaced, hidden or rebuilt without cutover in other contexts.
- Shared sensor lists live with the context that defines the term, not in a dashboard copy.
- Display-only concepts stay local to the Display package, even if they resemble another context's data.
- Tests check a Display against its documented prerequisites rather than letting hidden dependencies appear.
