"""Utility functions for parsing an OpenAPI spec into LangChain Tools / Toolkits."""
import copy
import json
import logging
import re
from typing import Dict, List, Optional, Tuple, Union
import requests

import tldextract
from openapi_schema_pydantic import (
    MediaType,
    OpenAPI,
    Operation,
    Parameter,
    Reference,
    RequestBody,
    Response,
    Schema,
)
from openapi_schema_pydantic import OpenAPI
from pydantic import ValidationError
import yaml

from langchain.requests import RequestsWrapper

logger = logging.getLogger(__name__)


class _OpenAPIModel(OpenAPI):
    """OpenAPI Model that removes misformatted parts of the spec."""

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


# TODO: Share with others
def _marshal_spec(txt: str) -> dict:
    """Convert the yaml or json serialized spec to a dict."""
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return yaml.safe_load(txt)


def _resolve_reference(
    ref: str, spec: OpenAPI
) -> Union[Schema, Parameter, RequestBody, Response]:
    """Resolve a $ref to a definition in the OpenAPI spec."""
    ref_name = ref.split("/")[-1]
    # TODO: The parsing library loses the `required` tags in the spec.
    if ref_name in spec.components.schemas:
        schema = spec.components.schemas[ref_name]
        _dereference_children(schema, spec)
        return schema
    # TODO: These probably also need recursive dereferencing
    component_types = [
        spec.components.parameters,
        spec.components.requestBodies,
        spec.components.responses,
    ]
    for component_type in component_types:
        if component_type is not None and ref_name in component_type:
            return component_type[ref_name]
    raise ValueError(f"Reference {ref} not found in spec")


def _dereference_anyof(schema: Schema, spec: OpenAPI) -> None:
    """Dereference anyOf schemas."""
    if schema.anyOf:
        resolved_any_of = []
        for any_of_schema in schema.anyOf:
            if isinstance(any_of_schema, Reference):
                resolved_any_of.append(_resolve_reference(any_of_schema.ref, spec))
            else:
                resolved_any_of.append(any_of_schema)
        schema.anyOf = resolved_any_of


def _dereference_properties(schema: Schema, spec: OpenAPI) -> None:
    """Dereference properties."""
    if schema.properties:
        resolved_properties = {}
        for prop_name, prop_schema in schema.properties.items():
            if isinstance(prop_schema, Reference):
                resolved_properties[prop_name] = _resolve_reference(
                    prop_schema.ref, spec
                )
            else:
                resolved_properties[prop_name] = prop_schema
            _dereference_children(resolved_properties[prop_name], spec)
        schema.properties = resolved_properties


def _dereference_children(schema: Schema, spec: OpenAPI) -> None:

    _dereference_anyof(schema, spec)
    _dereference_properties(schema, spec)


def _resolve_media_type_schema(
    content: Dict[str, MediaType], spec: OpenAPI
) -> Dict[str, Schema]:
    result = {}
    supported_encodings = content.keys()
    for encoding_style in supported_encodings:
        media_type = content[encoding_style]
        if not media_type:
            continue

        media_type_schema = media_type.media_type_schema
        if not media_type_schema:
            continue

        if isinstance(media_type_schema, Reference):
            request_body_schema = _resolve_reference(media_type_schema.ref, spec)
            if not request_body_schema.description:
                request_body_schema.description = media_type_schema.description
        else:
            request_body_schema = media_type_schema
        result[encoding_style] = request_body_schema
    return result


def _resolve_request_body_schema(
    operation: Operation,
    spec: OpenAPI,
) -> Tuple[Optional[Schema], Optional[str]]:
    if not operation.requestBody:
        return None, None
    content = operation.requestBody.content
    if not content:
        return None, None
    media_type_schema = _resolve_media_type_schema(content, spec)
    if not media_type_schema:
        return None, None
    encoding_style, media_type = media_type_schema.popitem()
    return media_type, encoding_style


def _resolve_query_params_schema(operation: Operation, spec: OpenAPI) -> Schema:
    if not operation.parameters:
        return Schema(type="object", properties={})

    query_params_schema_dict = {}
    required = set()
    for param in operation.parameters:
        if isinstance(param, Reference):
            resolved_param: Parameter = _resolve_reference(param.ref, spec)
            if resolved_param.required:
                required.add(resolved_param.name)
            schema = resolved_param.param_schema
            query_params_schema_dict[resolved_param.name] = schema
        elif param.param_schema is None:
            continue
        else:
            query_params_schema_dict[param.name] = resolve_schema(
                param.param_schema,
                spec,
                description=param.description,
            )
            if param.required:
                required.add(param.name)
    return Schema(
        type="object", properties=query_params_schema_dict, required=sorted(required)
    )


###### Shared functions #######


def extract_domain(url: str) -> str:
    """Extract domain from url."""
    extracted = tldextract.extract(url)
    return extracted.domain


def extract_path_params(path: str) -> List[str]:
    """Extract path parameters from a URI path like /path/to/{user_id}."""
    path_params_pattern = r"{(.*?)}"
    return re.findall(path_params_pattern, path)


def extract_query_params(operation: Operation) -> List[str]:
    """Extract parameter names from the request query of an operation."""
    query_params = []
    if operation.parameters is not None:
        for param in operation.parameters:
            if isinstance(param, Reference):
                name = param.ref.split("/")[-1]
            else:
                name = param.name
            query_params.append(name)

    return query_params


def extract_body_params(operation: Operation, spec: OpenAPI) -> List[str]:
    """Extract parameter names from the request body of an operation."""
    body_params = []
    request_body = operation.requestBody
    if request_body is None:
        return body_params

    if isinstance(request_body, Reference):
        name = request_body.ref.split("/")[-1]
        body_params.append(name)
        return body_params
    for content_type, json_content in request_body.content.items():
        media_type_schema = json_content.media_type_schema
        if media_type_schema is None:
            logger.debug(
                f"Content type '{content_type}' not supported"
                f" for operation {operation.operationId}."
                f"Supported types: {request_body.content.keys()}"
            )
            continue
        media_type_schema = resolve_schema(media_type_schema, spec)
        if media_type_schema.anyOf:
            for _schema_anyof in media_type_schema.anyOf:
                body_params.extend(_schema_anyof.properties.keys())
        elif media_type_schema.properties:
            body_params.extend(media_type_schema.properties.keys())
        else:
            logger.warning(
                f"No properties found for {media_type_schema}."
                " oneOf, allOf, and other attributes not yet implemented."
            )
    return body_params


def extract_query_and_body_params(
    operation: Operation, spec: OpenAPI
) -> Tuple[List[str], List[str]]:
    """Extract query and body parameters from an operation."""
    query_params = extract_query_params(operation)
    body_params = extract_body_params(operation, spec)
    return query_params, body_params


def generate_resolved_schema(
    operation: Operation, spec: OpenAPI
) -> Tuple[Schema, Optional[str]]:
    """Generate a combined schema object, dereferencing any references."""
    request_body_schema, encoding_type = _resolve_request_body_schema(operation, spec)
    query_params_schema = _resolve_query_params_schema(operation, spec)

    combined_schema = query_params_schema
    if request_body_schema:
        if request_body_schema.anyOf:
            for schema in request_body_schema.anyOf:
                combined_schema.properties.update(schema.properties)
        elif request_body_schema.properties:
            combined_schema.properties.update(request_body_schema.properties)
        else:
            logger.warning(
                "No properties in request body schema for operation"
                f" {operation.operationId}\n"
                f" oneOf, allOf, and other attributes not yet implemented."
            )
    return combined_schema, encoding_type


def _resolve_response(response: Response, spec: OpenAPI) -> Dict[str, Schema]:
    """Resolve a response object."""
    if response.content is None:
        return response
    return _resolve_media_type_schema(response.content, spec)


def generate_resolved_response_schema(
    operation: Operation, spec: OpenAPI
) -> Optional[MediaType]:
    """Generate a combined schema object, dereferencing any references."""
    if not operation.responses:
        return None
    response_schema = operation.responses.get("200")
    if response_schema is None:
        return None
    if isinstance(response_schema, Reference):
        # TODO: Not the right type actually
        response_schema = _resolve_reference(response_schema.ref, spec)
    if not isinstance(response_schema, Response):
        return
    schema_dict = _resolve_response(response_schema, spec)
    if not response_schema:
        return
    if "application/json" not in schema_dict:
        return
    json_schema = schema_dict["application/json"]
    resolved_schema = resolve_schema(json_schema, spec)
    return resolved_schema


def get_cleaned_operation_id(operation: Operation, path: str, verb: str) -> str:
    """Get a cleaned operation id from an operation id."""
    operation_id = operation.operationId
    if operation_id is None:
        # Replace all punctuation of any kind with underscore
        path = re.sub(r"[^a-zA-Z0-9]", "_", path.lstrip("/"))
        operation_id = f"{path}{verb.upper()}"
    return operation_id.replace("-", "_").replace(".", "_").replace("/", "_")


def resolve_schema(
    schema: Union[Schema, Reference],
    spec: OpenAPI,
    description: Optional[str] = None,
) -> Schema:
    """Resolve a schema or ref to a definition in the OpenAPI spec if needed."""
    _schema = schema
    if isinstance(schema, Reference):
        # TODO: the typing here is off since
        # the result of _resolve_reference may be a
        # parameter, reference, or response
        _schema = _resolve_reference(schema.ref, spec)
    if description and not _schema.description:
        _schema.description = description
    _dereference_children(_schema, spec)
    return _schema


def get_openapi_spec(url: str) -> OpenAPI:
    """Get an OpenAPI spec from a URL."""
    response = requests.get(url)
    open_api_spec = _marshal_spec(response.text)
    return _OpenAPIModel.parse_obj(open_api_spec)
