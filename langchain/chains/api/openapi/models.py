"""Pydantic models for parsing an OpenAPI spec."""

from enum import Enum
from typing import Dict, List, Optional, Sequence, Type, Union

from pydantic import BaseModel, Field, constr


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


# See: https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.1.0.md#parameterIn
class APIPropertyLocation(Enum):
    """The location of the property."""

    QUERY = "query"
    PATH = "path"
    HEADER = "header"
    COOKIE = "cookie"  # Not yet supported


class APIProperty(BaseModel):
    """A model for a property."""

    # Key within the 'parameters' dictionary.
    friendly_name: str = Field(alias="friendly_name")
    """The friendly name of the property."""

    # See Spec at above link.
    # The name of the parameter is required and is case sensitive.
    # If "in" is "path", the "name" field must correspond to a template expression within the path field in the Paths Object.
    # If "in" is "header" and the "name" field is "Accept", "Content-Type", or "Authorization", the parameter definition is ignored.
    # For all other cases, the "name" corresponds to the parameter name used by the "in" property.

    name: str = Field(alias="name")
    """The name of the property."""

    required: bool = Field(alias="required")
    """Whether the property is required."""

    type: Union[Type, BaseModel] = Field(alias="type")
    """The type of the property.
     
    Primitive types as strings. Object types are mapped
    to components."""

    default: Optional[str] = Field(alias="default", default=None)
    """The default value of the property."""

    location: APIPropertyLocation
    """The path/how it's being passed to the endpoint."""


class APIRequestBody(BaseModel):
    """A model for a request body."""

    content_type: str = Field(alias="content_type")
    """The content type of the request body."""

    properties: List[APIProperty] = Field(alias="properties")

    # E.g., application/json - we only support JSON at the moment.
    media_type: str = Field(alias="media_type")
    """The media type of the request body."""


class APIOperation(BaseModel):
    """A model for a single API operation."""

    operation_id: str = Field(alias="operation_id")
    """The unique identifier of the operation."""

    description: str = Field(alias="description")
    """The description of the operation."""

    base_url: str = Field(alias="base_url")
    """The base URL of the operation."""

    path: str = Field(alias="path")
    """The path of the operation."""

    method: HTTPVerb = constr(regex=r"^(get|put|post|delete|options|head|patch|trace)$")
    """The HTTP method of the operation."""

    properties: Sequence[APIProperty] = Field(alias="properties")
    """The properties of the operation."""

    # components: Dict[str, BaseModel] = Field(alias="components")
    # """The API components."""

    request_body: Optional[APIRequestBody] = Field(alias="request_body")
    """The request body of the operation."""

    # TODO: Add response body.
    # response_body: Optional[APIResponseBody] = Field(alias="responseBody")
    # """The response body of the operation."""


SUPPORTED_LOCATIONS = {
    APIPropertyLocation.QUERY,
    APIPropertyLocation.PATH,
    APIPropertyLocation.HEADER,
    APIPropertyLocation.COOKIE,
}
