from insteon.clean import AUTOMATION, DASHBOARD, SCENE, SCRIPT, strip

GONE = {"light.example_dimmer", "number.example_dimmer_on_level"}
GONE_DEVICES = {"dimmer"}


def stripped(config, kind=AUTOMATION):
    return strip(config, GONE, GONE_DEVICES, kind=kind)


def test_an_entity_is_taken_out_of_a_trigger_that_names_others_too() -> None:
    config = {
        "id": "1",
        "triggers": [{"trigger": "state", "entity_id": ["light.example_dimmer", "light.example_relay"]}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }

    result = stripped(config)

    assert result.refusal is None
    assert result.config["triggers"] == [{"trigger": "state", "entity_id": ["light.example_relay"]}]


def test_a_trigger_left_with_no_entity_is_deleted_and_the_others_stay() -> None:
    config = {
        "id": "1",
        "triggers": [
            {"trigger": "state", "entity_id": "light.example_dimmer"},
            {"trigger": "device", "device_id": "dimmer", "domain": "isy994", "type": "turned_on"},
            {"trigger": "state", "entity_id": "light.example_relay"},
        ],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": "light.example_dimmer"}, "data": {"brightness": 5}},
            {"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}},
        ],
    }

    result = stripped(config)

    assert result.config == {
        "id": "1",
        "triggers": [{"trigger": "state", "entity_id": "light.example_relay"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }


def test_a_condition_is_never_dropped_since_that_changes_when_the_automation_runs() -> None:
    config = {
        "id": "1",
        "triggers": [{"trigger": "state", "entity_id": "light.example_relay"}],
        "conditions": [
            {"condition": "or", "conditions": [{"condition": "state", "entity_id": "light.example_dimmer", "state": "on"}]}
        ],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }

    result = stripped(config)

    assert result.config is None
    assert "drop the condition at conditions[0].conditions[0]" in result.refusal


def test_an_automation_is_never_deleted_even_when_every_trigger_goes() -> None:
    config = {
        "id": "1",
        "triggers": [{"trigger": "state", "entity_id": "light.example_dimmer"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }

    result = stripped(config)

    assert result.config is None
    assert "triggers" in result.refusal and "edit it by hand" in result.refusal


def test_a_branch_whose_condition_goes_is_left_for_a_hand_edit() -> None:
    config = {
        "id": "1",
        "triggers": [{"trigger": "state", "entity_id": "light.example_relay"}],
        "actions": [
            {
                "if": [{"condition": "state", "entity_id": "light.example_dimmer", "state": "on"}],
                "then": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
            }
        ],
    }

    result = stripped(config)

    assert result.config is None
    assert "actions[0].if" in result.refusal


def test_a_template_naming_the_entity_is_left_for_a_hand_edit() -> None:
    config = {
        "id": "1",
        "triggers": [{"trigger": "template", "value_template": "{{ is_state('light.example_dimmer', 'on') }}"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }

    result = stripped(config)

    assert result.config is None
    assert "light.example_dimmer" in result.refusal and "triggers[0].value_template" in result.refusal


def test_a_config_that_does_not_name_the_device_is_unchanged() -> None:
    config = {"id": "1", "triggers": [{"trigger": "state", "entity_id": "light.example_relay"}], "actions": []}

    result = stripped(config)

    assert result.refusal is None and not result.changed


def test_a_script_left_with_no_actions_is_never_deleted() -> None:
    config = {"sequence": [{"action": "light.turn_off", "target": {"entity_id": "light.example_dimmer"}}]}

    result = stripped(config, SCRIPT)

    assert result.config is None and "sequence" in result.refusal


def test_an_entity_is_taken_out_of_a_scene_but_an_emptied_scene_stays() -> None:
    both = {"id": "9", "name": "Evening", "entities": {"light.example_dimmer": {"state": "on"}, "light.example_relay": {"state": "on"}}}
    only = {"id": "9", "name": "Evening", "entities": {"light.example_dimmer": {"state": "on"}}}

    assert stripped(both, SCENE).config == {"id": "9", "name": "Evening", "entities": {"light.example_relay": {"state": "on"}}}
    assert stripped(only, SCENE).config is None


def test_a_dashboard_card_left_empty_is_deleted_and_so_is_a_stack_it_empties() -> None:
    config = {
        "views": [
            {
                "title": "Home",
                "cards": [
                    {"type": "entities", "entities": ["light.example_dimmer", {"entity": "light.example_relay"}]},
                    {"type": "vertical-stack", "cards": [{"type": "light", "entity": "light.example_dimmer"}]},
                    {"type": "entities", "entities": [{"entity": "number.example_dimmer_on_level", "name": "Level"}]},
                ],
            }
        ]
    }

    result = stripped(config, DASHBOARD)

    assert result.config == {
        "views": [{"title": "Home", "cards": [{"type": "entities", "entities": [{"entity": "light.example_relay"}]}]}]
    }


def test_a_dashboard_is_never_deleted() -> None:
    config = {"views": [{"title": "Home", "cards": [{"type": "light", "entity": "light.example_dimmer"}]}]}

    assert stripped(config, DASHBOARD).config is None
