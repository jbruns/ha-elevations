# Dashboards

Dashboards owns the household Displays. The glossary is in [CONTEXT.md](./CONTEXT.md).

## Weather forecast package

`packages/dashboards_weather_forecast.yaml` is optional support for Displays. It creates hourly and daily forecast sensors from `weather.get_forecasts`, with forecasts held in each sensor's `forecast` attribute. Replace the placeholder `weather.example` with this home's weather entity before installing the package.

No blueprint depends on this package (ADR 0005).

### Cutover

1. Back up Home Assistant.
2. Copy `packages/dashboards_weather_forecast.yaml` into Home Assistant's packages, replacing `weather.example` with this home's weather entity.
3. Restart or reload package-backed YAML as usual for that installation.
4. Check that `sensor.dashboards_hourly_forecast` and `sensor.dashboards_daily_forecast` have a `forecast` attribute.
5. Remove the old `configuration.yaml` trigger-based template block that created the same forecast sensors.
