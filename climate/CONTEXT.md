# Climate

Keeps the home comfortable through its thermostat, and advises the household when to open or close the windows.

## Language

### Comfort

**Comfort Policy**:
The household's rules for the thermostat: what it should be set to, and how far a manual change may stray.
_Avoid_: HVAC automation, schedule

**Comfort Target**:
The temperature, or heating and cooling pair, that the Comfort Policy sets. It depends on the time of day, the forecast and indoor humidity.
_Avoid_: Setpoint, scheduled target

**Comfort Band**:
The weather-aware range a manual change may stay within. The Comfort Policy only pulls a setting back when it falls outside the Band, so it never undoes a reasonable manual change.
_Avoid_: Guardrail, limits

**Household Home**:
At least one person in the household is home.
_Avoid_: Occupied, presence

**Setback**:
The wider, energy-saving thermostat setting the Comfort Policy applies once the household has been away for a while. The Comfort Target returns when someone comes home.
_Avoid_: Away mode, eco

### Doors and windows

**Exterior Door**:
A door or slider between the home and the outdoors, watched by a sensor that reads *on* while it is open.
_Avoid_: Entry, contact

**Door Pause**:
The thermostat is turned off because an Exterior Door has been open too long. When every Exterior Door has been closed for a while, the Door Pause ends and the thermostat returns to its earlier mode. The Comfort Policy makes no changes during a Door Pause.
_Avoid_: HVAC off, door hold

**Ventilation Recommendation**:
Advice to open the windows, because outdoor air would improve indoor comfort over the next few hours, or to close them again. An "open" is always followed by a "close".
_Avoid_: Window alert, open-the-windows
