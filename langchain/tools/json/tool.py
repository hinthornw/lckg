"""Tools for interacting with JSON data."""


import json
from typing import Any, List

from langchain.tools.tool import Tool


class JsonGetSchemaTool(Tool):
    """Tool for getting the schema of a JSON file."""

    name = "json_get_schema"
    description = "Get the schema of a JSON file."
    data: dict

    def func(self, *args: Any, **kwargs: Any) -> str:
        """Get the schema of a JSON file up to the specified depth."""
        return json.dumps({key: type(value) for key, value in self.data.items()})


class JsonGetKeysTool(Tool):
    """Tool for getting the keys of a JSON file."""

    name = "json_get_keys"
    description = "Get the keys of a JSON file."
    data: dict

    def func(self, *args: Any, **kwargs: Any) -> str:
        """Get the keys of a JSON file."""
        return str(json.dumps(list(self.data.keys())))


class JsonGetValuesTool(Tool):
    """Tool for getting the values of a JSON file."""

    name = "json_get_values"
    description = "Get the values of a JSON file."
    data: dict

    def func(self, *args: Any, **kwargs: Any) -> str:
        """Get the values of a JSON file."""
        return json.dumps(list(self.data.values()))
