"""Toolkit for interacting with API's using natural language."""


from typing import Any, List, Optional

from pydantic import Field

from langchain.agents.agent_toolkits.base import BaseToolkit
from langchain.agents.agent_toolkits.nla.tool import NLATool
from langchain.llms.base import BaseLLM
from langchain.requests import Requests
from langchain.tools.base import BaseTool
from langchain.tools.openapi.utils.openapi_utils import OpenAPISpec


class NLAToolkit(BaseToolkit):
    """Natural Language API Toolkit Definition."""

    nla_tools: List[NLATool] = Field(...)
    """List of API Endpoint Tools."""

    def get_tools(self) -> List[BaseTool]:
        """Get the tools for all the API operations."""
        return self.nla_tools

    @classmethod
    def from_llm_and_spec(
        cls,
        llm: BaseLLM,
        spec: OpenAPISpec,
        requests: Optional[Requests] = None,
        verbose: bool = False,
        **kwargs: Any
    ) -> "NLAToolkit":
        """Instantiate the toolkit by creating tools for each operation."""
        http_operation_tools = []
        for path in spec.paths:
            for method in spec.get_methods_for_path(path):
                endpoint_tool = NLATool.from_llm_and_method(
                    llm=llm,
                    path=path,
                    method=method,
                    spec=spec,
                    requests=requests,
                    verbose=verbose,
                    **kwargs
                )
                http_operation_tools.append(endpoint_tool)
        return cls(nla_tools=http_operation_tools)

    @classmethod
    def from_llm_and_url(
        cls,
        llm: BaseLLM,
        open_api_url: str,
        requests: Optional[Requests] = None,
        verbose: bool = False,
        **kwargs: Any
    ) -> "NLAToolkit":
        """Instantiate the toolkit from an OpenAPI Spec URL"""
        spec = OpenAPISpec.from_url(open_api_url)
        return cls.from_llm_and_spec(
            llm=llm, spec=spec, requests=requests, verbose=verbose, **kwargs
        )