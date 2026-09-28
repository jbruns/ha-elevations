# Blueprints compute derived values themselves instead of depending on template sensors

The live automations depended on template sensors and scripts: who is home (from named phones), the 12-hour forecast high and low, and the window ventilation recommendation. Those sensors made the automations fragile, since renaming a phone broke occupancy, and they can't be shared: a blueprint can't create a sensor, so everyone who used it would first have to copy our templates. So the blueprints work these values out when they run. They read `zone.home` for Household Home, call `weather.get_forecasts` for the forecast, and evaluate the Ventilation Recommendation inside the automation. The only state they need from outside is ordinary helpers (`input_boolean`, `input_select`, `input_datetime`), which anyone can create in the UI.

## Consequences

- No dashboard can show the ventilation reason or the forecast high and low as a sensor. If one needs them later, ship a documented package template alongside, without making the blueprint depend on it.
- Household Home now means "any person in `zone.home`", not "either of two named phones".
