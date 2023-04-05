"""Utility functions for parsing an OpenAPI spec."""
import copy
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Dict, List, Union

import requests
import yaml
from openapi_schema_pydantic import (
    Components,
    OpenAPI,
    Operation,
    Parameter,
    PathItem,
    Paths,
    Reference,
    Schema,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)


class HTTPVerb(str, Enum):
    """HTTP verbs."""

    GET = "get"
    PUT = "put"
    POST = "post"
    DELETE = "delete"
    OPTIONS = "options"
    HEAD = "head"
    PATCH = "patch"
    TRACE = "trace"

    @classmethod
    def from_str(cls, verb: str) -> "HTTPVerb":
        """Parse an HTTP verb."""
        try:
            return cls(verb)
        except ValueError:
            raise ValueError(f"Invalid HTTP verb. Valid values are {cls.__members__}")


class OpenAPISpec(OpenAPI):
    """OpenAPI Model that removes misformatted parts of the spec."""

    @property
    def _paths_strict(self) -> Paths:
        if not self.paths:
            raise ValueError("No paths found in spec")
        return self.paths

    def _get_path_strict(self, path: str) -> PathItem:
        path = self._paths_strict.get(path)
        if not path:
            raise ValueError(f"No path found for {path}")
        return path

    @property
    def _components_strict(self) -> Components:
        """Get components or err."""
        if self.components is None:
            raise ValueError("No components found in spec. ")
        return self.components

    @property
    def _parameters_strict(self) -> Dict[str, Union[Parameter, Reference]]:
        """Get parameters or err."""
        parameters = self._components_strict.parameters
        if parameters is None:
            raise ValueError("No parameters found in spec. ")
        return parameters

    @property
    def _schema_strict(self) -> Dict[str, Schema]:
        """Get the sch"""
        schemas = self._components_strict.schemas
        if schemas is None:
            raise ValueError("No schemas found in spec. ")
        return schemas

    def _get_referenced_parameter(self, ref: Reference) -> Union[Parameter, Reference]:
        """Get a parameter (or nested reference) or err."""
        ref_name = ref.ref.split("/")[-1]
        if ref_name not in self._parameters_strict:
            raise ValueError(f"No parameter found for {ref_name}")
        return self.components.parameters[ref_name]

    def _get_root_referenced_parameter(self, ref: Reference) -> Parameter:
        """Get the root reference or err."""
        parameter = self._get_referenced_parameter(ref)
        while isinstance(parameter, Reference):
            parameter = self._get_referenced_parameter(parameter)
        return parameter

    def get_referenced_schema(self, ref: Reference) -> Schema:
        """Get a schema (or nested reference) or err."""
        ref_name = ref.ref.split("/")[-1]
        if ref_name not in self._schema_strict:
            raise ValueError(f"No schema found for {ref_name}")
        return self.components.schemas[ref_name]

    def _get_root_referenced_schema(self, ref: Reference) -> Schema:
        """Get the root reference or err."""
        schema = self.get_referenced_schema(ref)
        while isinstance(schema, Reference):
            schema = self.get_referenced_schema(schema)
        return schema

    @classmethod
    def parse_obj(cls, obj):
        try:
            return super().parse_obj(obj)
        except ValidationError as e:
            # We are handling possibly misconfigured specs and want to do a best-effort
            # job to get a reasonable interface out of it.
            new_obj = copy.deepcopy(obj)
            for error in e.errors():
                keys = error["loc"]
                item = new_obj
                for key in keys[:-1]:
                    item = item[key]
                item.pop(keys[-1], None)
            return cls.parse_obj(new_obj)

    @classmethod
    def from_spec_dict(cls, spec_dict: dict) -> "OpenAPISpec":
        """Get an OpenAPI spec from a dict."""
        return cls.parse_obj(spec_dict)

    @classmethod
    def from_text(cls, text: str) -> "OpenAPISpec":
        """Get an OpenAPI spec from a text."""
        try:
            spec_dict = json.loads(text)
        except json.JSONDecodeError:
            spec_dict = yaml.safe_load(text)
        return cls.from_spec_dict(spec_dict)

    @classmethod
    def from_file(cls, path: str) -> "OpenAPISpec":
        """Get an OpenAPI spec from a file path."""
        path_ = Path(path)
        if not path_.exists():
            raise FileNotFoundError(f"{path} does not exist")
        with path_.open("r") as f:
            return cls.from_text(f.read())

    @classmethod
    def from_url(cls, url: str) -> "OpenAPISpec":
        """Get an OpenAPI spec from a URL."""
        response = requests.get(url)
        return cls.from_text(response.text)

    @property
    def base_url(self) -> str:
        """Get the base url."""
        return self.servers[0].url

    def get_methods_for_path(self, path: str) -> List[str]:
        """Return a list of valid methods for the specified path."""
        path_item = self._get_path_strict(path)
        results = []
        for method in HTTPVerb:
            operation = getattr(path_item, method.value, None)
            if isinstance(operation, Operation):
                results.append(method.value)
        return results

    def get_operation(self, path: str, method: str) -> Operation:
        """Get the operation object for a given path and HTTP method."""
        path_item = self._get_path_strict(path)
        operation_obj = getattr(path_item, method, None)
        if not isinstance(operation_obj, Operation):
            raise ValueError(f"No {method} method found for {path}")
        return operation_obj

    def get_parameters_for_operation(self, operation: Operation) -> List[Parameter]:
        """Get the components for a given operation."""
        parameters = []
        if operation.parameters:
            for parameter in operation.parameters:
                if isinstance(parameter, Reference):
                    parameter = self._get_root_referenced_parameter(parameter)
                parameters.append(parameter)
        return parameters