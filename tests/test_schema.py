"""Tests for JSON schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, ValidationError, validate


@pytest.fixture
def schema() -> dict:
    """Load the config schema."""
    schema_path = Path(__file__).parent.parent / "config" / "config.schema.json"
    with open(schema_path) as f:
        return json.load(f)


@pytest.fixture
def config() -> dict:
    """Load the actual config file."""
    config_path = Path(__file__).parent.parent / "config" / "config.json"
    with open(config_path) as f:
        return json.load(f)


class TestSchemaValidity:
    """Tests that the schema itself is valid."""

    def test_schema_is_valid_draft7(self, schema: dict) -> None:
        """Schema should be valid JSON Schema Draft 7."""
        Draft7Validator.check_schema(schema)


class TestConfigValidation:
    """Tests that config.json validates against the schema."""

    def test_config_validates_against_schema(self, schema: dict, config: dict) -> None:
        """config.json should validate against config.schema.json."""
        validate(instance=config, schema=schema)

    def test_config_has_watchers(self, config: dict) -> None:
        """Config must have at least one watcher."""
        assert "watchers" in config
        assert len(config["watchers"]) >= 1

    def test_all_watchers_have_required_fields(self, config: dict) -> None:
        """Each watcher must have name, watch, and pipeline."""
        for watcher in config["watchers"]:
            assert "name" in watcher, f"Watcher missing 'name'"
            assert "watch" in watcher, f"Watcher '{watcher.get('name')}' missing 'watch'"
            assert "pipeline" in watcher, f"Watcher '{watcher.get('name')}' missing 'pipeline'"

    def test_all_scripts_have_valid_type(self, config: dict) -> None:
        """Each script must have a valid type."""
        valid_types = {"applescript", "python", "command"}
        for watcher in config["watchers"]:
            for script in watcher["pipeline"].get("scripts", []):
                assert script["type"] in valid_types, (
                    f"Script '{script['name']}' has invalid type: {script['type']}"
                )


class TestSchemaRejectsInvalid:
    """Tests that schema correctly rejects invalid configs."""

    def test_rejects_empty_watchers(self, schema: dict) -> None:
        """Schema should reject empty watchers array."""
        invalid_config = {"watchers": []}
        with pytest.raises(ValidationError, match="non-empty"):
            validate(instance=invalid_config, schema=schema)

    def test_rejects_missing_watchers(self, schema: dict) -> None:
        """Schema should reject config without watchers."""
        invalid_config = {"logging": {"level": "INFO"}}
        with pytest.raises(ValidationError, match="'watchers' is a required property"):
            validate(instance=invalid_config, schema=schema)

    def test_rejects_invalid_script_type(self, schema: dict) -> None:
        """Schema should reject invalid script type."""
        invalid_config = {
            "watchers": [{
                "name": "Test",
                "watch": {"base_folder": "~/test"},
                "pipeline": {
                    "scripts": [{
                        "name": "Bad Script",
                        "type": "invalid_type",
                        "path": "test.py"
                    }]
                }
            }]
        }
        with pytest.raises(ValidationError, match="'invalid_type' is not one of"):
            validate(instance=invalid_config, schema=schema)

    def test_rejects_invalid_log_level(self, schema: dict) -> None:
        """Schema should reject invalid log level."""
        invalid_config = {
            "watchers": [{
                "name": "Test",
                "watch": {"base_folder": "~/test"},
                "pipeline": {"scripts": []}
            }],
            "logging": {"level": "INVALID"}
        }
        with pytest.raises(ValidationError, match="'INVALID' is not one of"):
            validate(instance=invalid_config, schema=schema)

    def test_rejects_watcher_missing_name(self, schema: dict) -> None:
        """Schema should reject watcher without name."""
        invalid_config = {
            "watchers": [{
                "watch": {"base_folder": "~/test"},
                "pipeline": {"scripts": []}
            }]
        }
        with pytest.raises(ValidationError, match="'name' is a required property"):
            validate(instance=invalid_config, schema=schema)

    def test_rejects_watcher_missing_watch(self, schema: dict) -> None:
        """Schema should reject watcher without watch section."""
        invalid_config = {
            "watchers": [{
                "name": "Test",
                "pipeline": {"scripts": []}
            }]
        }
        with pytest.raises(ValidationError, match="'watch' is a required property"):
            validate(instance=invalid_config, schema=schema)

    def test_rejects_watch_missing_base_folder(self, schema: dict) -> None:
        """Schema should reject watch without base_folder."""
        invalid_config = {
            "watchers": [{
                "name": "Test",
                "watch": {},
                "pipeline": {"scripts": []}
            }]
        }
        with pytest.raises(ValidationError, match="'base_folder' is a required property"):
            validate(instance=invalid_config, schema=schema)
