"""OpenAPI Tool wrapper."""


from typing import Dict

from pydantic import BaseModel
from langchain.agents.tools import Tool


class OpenAPITool(Tool):

    request_models: Dict[str, BaseModel]
