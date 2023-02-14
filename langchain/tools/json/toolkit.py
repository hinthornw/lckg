"""Toolkit for interacting with JSON data."""

from typing import List

from langchain.tools.json.tool import (
    JsonGetKeysTool,
    JsonGetSchemaTool,
    JsonGetValuesTool,
)
from langchain.tools.tool import Tool
from langchain.tools.toolkit import Toolkit


class JsonToolkit(Toolkit):
    """Toolkit for interacting with JSON data."""

    data: dict

    def get_tools(self) -> List[Tool]:
        """Get the tools in the toolkit."""
        classes = (JsonGetKeysTool, JsonGetSchemaTool, JsonGetValuesTool)
        return [_cls(data=self.data) for _cls in classes]
