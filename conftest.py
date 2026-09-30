# The shared test harness: blueprint installation, then the fixtures every
# context's tests can use.
pytest_plugins = [
    "testing.blueprints",
    "testing.phones",
    "testing.clock",
    "testing.helpers",
    "testing.sensors",
    "testing.household",
    "testing.weather",
    "testing.calendar",
    "testing.thermostat",
    "testing.lights",
]
