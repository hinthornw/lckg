"""Parse an OpenAPI spec."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Union

import requests
import yaml
from pydantic import BaseModel

from langchain.chains.api.openapi.parsing.models import (
    APIOperation,
    APIProperty,
    APIPropertyLocation,
    HTTPVerb,
)

logger = logging.getLogger(__file__)


class APIParsingError(Exception):
    """An error occurred during parsing."""


# TODO: Share this with other folders.
def _marshal_spec(txt: str) -> dict:
    """Convert the yaml or json serialized spec to a dict."""
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return yaml.safe_load(txt)


def _get_cleaned_operation_id(operation_id: Optional[str], path: str, verb: str) -> str:
    """Get a cleaned operation id from an operation id."""
    if operation_id is None:
        # Replace all punctuation of any kind with underscore
        path = re.sub(r"[^a-zA-Z0-9]", "_", path.lstrip("/"))
        operation_id = f"{path}{verb.upper()}"
    return operation_id.replace("-", "_").replace(".", "_").replace("/", "_")


def _get_base_url_from_spec(spec: dict) -> str:
    """Get the base url from the spec."""
    servers = spec.get("servers", [])
    if len(servers) > 1:
        logging.Logger.warning(
            f"Multiple servers found in OpenAPI Spec."
            " Selecting first, likely resulting in downstream errors."
        )
    base_url = servers[0].get("url", "").strip()
    if not base_url:
        raise APIParsingError(
            f"No base URL found in OpenAPI Spec. Found servers: {servers}"
        )
    return base_url


def _create_pydantic_model(name: str, fields: Dict[str, Any]) -> Any:
    if "schema" in fields:  # Shadows a BaseModel attribute
        fields["schema_"] = fields.pop("schema")
    return BaseModel.__class__(name, (BaseModel,), fields)


def _create_model_from_fields(
    name: str,
    properties: dict,
    required: list,
    result: Dict[str, Any],
    seen: Optional[Set[str]] = None,
) -> Any:
    fields = {}
    for prop_name, prop_def in properties.items():
        if isinstance(prop_def, dict):
            prop_type = _get_pydantic_type(prop_def, result, spec, seen=seen)
        else:  # Is already converted
            prop_type = prop_def
        if prop_name in required:
            fields[prop_name] = (prop_type, ...)
        else:
            fields[prop_name] = (prop_type, None)

    return _create_pydantic_model(name, fields)


def _create_component_model(
    name: str,
    model_def: Dict[str, Any],
    result: Dict[str, Any],
    seen: Optional[Set[str]] = None,
) -> Any:
    seen = seen if seen is not None else set()
    if name in seen:
        # Cycle detected in the spec definitions
        return name
    seen.add(name)
    if "anyOf" in model_def or "oneOf" in model_def:
        key = "anyOf" if "anyOf" in model_def else "oneOf"
        types = [_get_pydantic_type(s, result, spec, seen=seen) for s in model_def[key]]
        if len(types) == 1:
            return types[0]
        else:
            return Union[*types]  # type: ignore
    elif "allOf" in model_def:
        all_properties = {}
        all_required = []
        for s in model_def["allOf"]:
            if "properties" in s:
                all_properties.update(s["properties"])
                all_required.extend(s.get("required", []))
            elif "$ref" in s:
                ref_model = _get_pydantic_type(s, result, spec, seen=seen)
                all_properties.update(ref_model.__fields__)
                all_required.extend(
                    [
                        field
                        for field, value in ref_model.__fields__.items()
                        if value.default is ...
                    ]
                )
            elif s.get("type") in {"object", None}:
                # Unqualified object means it accepts an arbitrary dict object
                # but this won't be helpful to the LLM really.
                # And if it's None then.... that's the API owner's choice.
                continue
            else:
                raise ValueError(f"Unsupported schema in allOf: {s}")
        properties = all_properties
        required = all_required
    elif model_def.get("type") == "object" or model_def.get("properties"):
        properties = model_def.get("properties", {})
        required = model_def.get("required", [])
    elif model_def.get("type") in PRIMITIVE_TYPES:
        # No need to generate a pydantic object for top-level primitive types
        return PRIMITIVE_TYPES[model_def.get("type")]
    elif model_def.get("type") == "array":
        item_type = _get_pydantic_type(model_def["items"], result, seen=seen)
        return _create_pydantic_model(name, {"items": (List[item_type], ...)})
    elif model_def.get("type") == None:
        # Couldn't be that important if it's not documented now, could it?
        return None
    else:
        raise ValueError(f"Unsupported model definition: {model_def}")

    return _create_model_from_fields(name, properties, required, result, seen=seen)


def _get_pydantic_type(
    schema: Dict[str, Any],
    result: Dict[str, Any],
    spec: dict,
    seen: Optional[Set] = None,
) -> Any:
    seen = seen if seen is not None else set()
    if "$ref" in schema:
        ref_path = schema["$ref"].split("/")
        ref_model_name = ref_path[-1]
        if ref_model_name not in result:
            ref_model_def = spec
            for p in ref_path[1:]:
                ref_model_def = ref_model_def[p]
            if ref_model_def.get("type") in PRIMITIVE_TYPES:
                return PRIMITIVE_TYPES[ref_model_def.get("type")]
            if ref_model_name in result:
                # Guard against infinite recursion
                return result[ref_model_name]
            elif ref_model_name in seen:
                # Going to have to use a forward reference here.
                # We have a cycle in the spec.
                return ref_model_name
            else:
                result[ref_model_name] = _create_component_model(
                    ref_model_name, ref_model_def, result, seen=seen
                )
        return result[ref_model_name]
    schema_type = schema.get("type")
    if schema_type == "object" or schema.get("properties"):
        return _create_component_model("AnonymousModel", schema, result, seen=seen)
    elif schema_type == "array":
        return list[_get_pydantic_type(schema["items"], result, spec, seen=seen)]
    elif schema_type == "integer":
        return int
    elif schema_type == "number":
        return float
    elif schema_type == "string":
        return str
    elif schema_type == "boolean":
        return bool
    elif schema_type == "null":
        return None
    elif "oneOf" in schema or "anyOf" in schema:
        key = "oneOf" if "oneOf" in schema else "anyOf"
        # Ignore the linting because Union doesn't recognize the tuple args
        return Union[  # type: ignore
            tuple(
                [
                    _get_pydantic_type(sub_schema, result, spec, seen=seen)
                    for sub_schema in schema[key]
                ]
            )
        ]
    elif "allOf" in schema:
        all_properties = {}
        all_required = []
        prim_types = []
        for s in schema["allOf"]:
            if "properties" in s:
                all_properties.update(s["properties"])
                all_required.extend(s.get("required", []))
            elif "$ref" in s:
                ref_model = _get_pydantic_type(s, result, spec)
                if hasattr(ref_model, "__fields__"):
                    # Generated type
                    all_properties.update(ref_model.__fields__)
                    all_required.extend(
                        [
                            field
                            for field, value in ref_model.__fields__.items()
                            if value.default is ...
                        ]
                    )
                else:
                    prim_types.append(ref_model)
            else:
                logger.warning(f"Unsupported schema in allOf: {s}")
                continue
        if prim_types:
            if all_properties:
                raise APIParsingError(
                    f"Cannot mix primitives and non-primitives in allOf: {schema}"
                )
            if len(prim_types) == 1:
                return prim_types[0]
            else:
                raise APIParsingError(
                    f"Cannot have multiple primitive types in an allOf: {schema}"
                )
        # Object types
        all_properties = all_properties
        allof_def = {
            "properties": all_properties,
            "type": "object",
            "required": all_required,
        }
        return _create_component_model("MergedModel", allof_def, result, seen=seen)
    elif schema_type is None:
        # It couldn't be that important now could it?
        return None
    else:
        raise ValueError(f"Unsupported schema: {schema}")


def extract_components(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the components from an OpenAPI spec."""

    result = {}
    for model_name, model_def in spec.get("components", {}).get("schemas", {}).items():
        result[model_name] = _create_component_model(model_name, model_def, result)

    return result


################ Public API #################


def get_spec_from_url(url: str) -> dict:
    """Parse an OpenAPI spec from a URL."""
    text = requests.get(url).text
    return _marshal_spec(text)


def get_spec_from_file(file_path: str) -> dict:
    """Parse an OpenAPI spec from a file."""
    with open(file_path, "r") as f:
        text = f.read()
    return _marshal_spec(text)


PRIMITIVE_TYPES = {
    "number": float,
    "integer": int,
    "string": str,
    "boolean": bool,
    "null": None,
}


# def extract_components(spec: Dict[str, Any]) -> Dict[str, Any]:
#     """Parse the components from an OpenAPI spec."""

# def creat_model_from_fields(
#     name: str, properties: dict, required: list, seen: Optional[Set[str]] = None
# ) -> Any:
#     fields = {}
#     for prop_name, prop_def in properties.items():
#         if isinstance(prop_def, dict):
#             prop_type = get_pydantic_type(prop_def, seen=seen)
#         else:  # Is already converted
#             prop_type = prop_def
#         if prop_name in required:
#             fields[prop_name] = (prop_type, ...)
#         else:
#             fields[prop_name] = (prop_type, None)

#     return create_pydantic_model(name, fields)

#     def create_component_model(
#         name: str, model_def: Dict[str, Any], seen: Optional[Set[str]] = None
#     ) -> Any:
#         seen = seen if seen is not None else set()
#         if name in seen:
#             # Cycle detected in the spec definitions
#             return name
#         seen.add(name)
#         if "anyOf" in model_def or "oneOf" in model_def:
#             key = "anyOf" if "anyOf" in model_def else "oneOf"
#             types = [get_pydantic_type(s, seen=seen) for s in model_def[key]]
#             if len(types) == 1:
#                 return types[0]
#             else:
#                 return Union[*types]  # type: ignore
#         elif "allOf" in model_def:
#             all_properties = {}
#             all_required = []
#             for s in model_def["allOf"]:
#                 if "properties" in s:
#                     all_properties.update(s["properties"])
#                     all_required.extend(s.get("required", []))
#                 elif "$ref" in s:
#                     ref_model = get_pydantic_type(s, seen=seen)
#                     all_properties.update(ref_model.__fields__)
#                     all_required.extend(
#                         [
#                             field
#                             for field, value in ref_model.__fields__.items()
#                             if value.default is ...
#                         ]
#                     )
#                 elif s.get("type") in {"object", None}:
#                     # Unqualified object means it accepts an arbitrary dict object
#                     # but this won't be helpful to the LLM really.
#                     # And if it's None then.... that's the API owner's choice.
#                     continue
#                 else:
#                     raise ValueError(f"Unsupported schema in allOf: {s}")
#             properties = all_properties
#             required = all_required
#         elif model_def.get("type") == "object" or model_def.get("properties"):
#             properties = model_def.get("properties", {})
#             required = model_def.get("required", [])
#         elif model_def.get("type") in PRIMITIVE_TYPES:
#             # No need to generate a pydantic object for top-level primitive types
#             return PRIMITIVE_TYPES[model_def.get("type")]
#         elif model_def.get("type") == "array":
#             item_type = get_pydantic_type(model_def["items"], seen=seen)
#             return create_pydantic_model(name, {"items": (List[item_type], ...)})
#         elif model_def.get("type") == None:
#             # Couldn't be that important if it's not documented now, could it?
#             return None
#         else:
#             raise ValueError(f"Unsupported model definition: {model_def}")

#         return creat_model_from_fields(name, properties, required, seen=seen)

#     def get_pydantic_type(schema: Dict[str, Any], seen: Optional[Set] = None) -> Any:
#         seen = seen if seen is not None else set()
#         if "$ref" in schema:
#             ref_path = schema["$ref"].split("/")
#             ref_model_name = ref_path[-1]
#             if ref_model_name not in result:
#                 ref_model_def = spec
#                 for p in ref_path[1:]:
#                     ref_model_def = ref_model_def[p]
#                 if ref_model_def.get("type") in PRIMITIVE_TYPES:
#                     return PRIMITIVE_TYPES[ref_model_def.get("type")]
#                 if ref_model_name in result:
#                     # Guard against infinite recursion
#                     return result[ref_model_name]
#                 elif ref_model_name in seen:
#                     # Going to have to use a forward reference here.
#                     # We have a cycle in the spec.
#                     return ref_model_name
#                 else:
#                     result[ref_model_name] = create_component_model(
#                         ref_model_name, ref_model_def, seen=seen
#                     )
#             return result[ref_model_name]
#         schema_type = schema.get("type")
#         if schema_type == "object" or schema.get("properties"):
#             return create_component_model("AnonymousModel", schema, seen=seen)
#         elif schema_type == "array":
#             return list[get_pydantic_type(schema["items"], seen=seen)]
#         elif schema_type == "integer":
#             return int
#         elif schema_type == "number":
#             return float
#         elif schema_type == "string":
#             return str
#         elif schema_type == "boolean":
#             return bool
#         elif schema_type == "null":
#             return None
#         elif "oneOf" in schema or "anyOf" in schema:
#             key = "oneOf" if "oneOf" in schema else "anyOf"
#             # Ignore the linting because Union doesn't recognize the tuple args
#             return Union[  # type: ignore
#                 tuple(
#                     [
#                         get_pydantic_type(sub_schema, seen=seen)
#                         for sub_schema in schema[key]
#                     ]
#                 )
#             ]
#         elif "allOf" in schema:
#             all_properties = {}
#             all_required = []
#             prim_types = []
#             for s in schema["allOf"]:
#                 if "properties" in s:
#                     all_properties.update(s["properties"])
#                     all_required.extend(s.get("required", []))
#                 elif "$ref" in s:
#                     ref_model = get_pydantic_type(s)
#                     if hasattr(ref_model, "__fields__"):
#                         # Generated type
#                         all_properties.update(ref_model.__fields__)
#                         all_required.extend(
#                             [
#                                 field
#                                 for field, value in ref_model.__fields__.items()
#                                 if value.default is ...
#                             ]
#                         )
#                     else:
#                         prim_types.append(ref_model)
#                 else:
#                     logger.warning(f"Unsupported schema in allOf: {s}")
#                     continue
#             if prim_types:
#                 if all_properties:
#                     raise APIParsingError(
#                         f"Cannot mix primitives and non-primitives in allOf: {schema}"
#                     )
#                 if len(prim_types) == 1:
#                     return prim_types[0]
#                 else:
#                     raise APIParsingError(
#                         f"Cannot have multiple primitive types in an allOf: {schema}"
#                     )
#             # Object types
#             all_properties = all_properties
#             allof_def = {
#                 "properties": all_properties,
#                 "type": "object",
#                 "required": all_required,
#             }
#             return create_component_model("MergedModel", allof_def, seen=seen)
#         elif schema_type is None:
#             # It couldn't be that important now could it?
#             return None
#         else:
#             raise ValueError(f"Unsupported schema: {schema}")

#     def create_pydantic_model(name: str, fields: Dict[str, Any]) -> Any:
#         if "schema" in fields:  # Shadows a BaseModel attribute
#             fields["schema_"] = fields.pop("schema")
#         return BaseModel.__class__(name, (BaseModel,), fields)

#     result = {}
#     for model_name, model_def in spec.get("components", {}).get("schemas", {}).items():
#         result[model_name] = create_component_model(model_name, model_def)
#     return result


def _create_pydantic_model_from_parameter(
    name: str, parameter: dict, parameters: Dict[str, Optional[APIProperty]]
) -> None:
    """Create an APIProperty model from an OpenAPI parameter."""
    required = parameter.get("required", False)
    param_name = parameter["name"]  # E.g., {'id'} if a path param
    location = APIPropertyLocation(parameter["in"])

    schema = parameter["schema"]
    if "type" in schema:
        # TODO: More nesting
        type_ = PRIMITIVE_TYPES[schema["type"]]
    elif "$ref" in schema:
        # TODO: Handle refs to other components
        ref_name = schema["$ref"].split("/")[-1]
        if ref_name not in parameter:
            raise APIParsingError(
                f"Could not resolve reference '{ref_name}'\n"
                "Expected a primitive type or one of:"
                f" {sorted(parameters.keys())}"
            )
        if parameters[ref_name] is None:
            # DFS to resolve other parameters
            _create_pydantic_model_from_parameter(ref_name, parameters)
        type_ = parameters[ref_name]
    else:
        raise NotImplementedError(
            f"Could not determine type of parameter '{name}'" f" in schema '{schema}'"
        )

    default = schema.get("default", None)
    parameters[name] = APIProperty(
        friendly_name=name,
        name=param_name,
        required=required,
        type=type_,
        default=default,
        location=location,
    )


def _parse_spec_parameters(spec_parameters: dict, parameters: Dict[str, None]) -> None:
    """Parse the spec parameters."""
    param_names = sorted(spec_parameters.keys())
    for name in param_names:
        param = spec_parameters[name]
        if parameters.get(name) is None:
            _create_pydantic_model_from_parameter(name, param, parameters)


def _extract_api_properties(
    operation: dict,
    components: dict,
) -> List[APIProperty]:
    """Extract the API properties from an operation."""
    properties = []
    for param in operation.get("parameters", []):
        if "$ref" in param:
            # Check model_dict.parameters
            param_name = param["$ref"].split("/")[-1]
            if param_name not in components:
                raise APIParsingError(
                    f"Could not resolve reference '{param_name}'\n"
                    " Expected a primitive type or one of:"
                    f" {sorted(components.keys())}"
                )
            property_ = components[param_name]
        else:
            schema = param["schema"]
            type_ = PRIMITIVE_TYPES[schema["type"]]
            property_ = APIProperty(
                friendly_name=param["name"],
                name=param["name"],
                required=param.get("required", False),
                type=type_,
                default=schema.get("default"),
                location=APIPropertyLocation(param["in"]),
            )
        properties.append(property_)
    return properties


def get_full_components_dict(spec: dict) -> dict:
    """Get the full components dict from the spec."""
    full_components_dict = extract_components(spec)
    _parse_spec_parameters(
        spec.get("components", {}).get("parameters", {}), full_components_dict
    )
    return full_components_dict


def parse_path(
    spec: dict, path: str, verb: str, full_components_dict: Optional[dict] = None
):
    operation = spec.get("paths", {}).get(path, {}).get(verb)
    if not operation:
        return
    if not full_components_dict:
        full_components_dict = extract_components(spec)
        _parse_spec_parameters(
            spec.get("components", {}).get("parameters", {}), full_components_dict
        )
    properties = _extract_api_properties(operation, full_components_dict)
    http_verb = HTTPVerb.from_str(verb)
    operation_id = _get_cleaned_operation_id(
        operation.get("operationId"), path, http_verb
    )
    base_url = _get_base_url_from_spec(spec)
    request_body = None  # TODO:
    # TODO: Parse in examples
    return APIOperation(
        operation_id=operation_id,
        description=operation.get("description", ""),
        base_url=base_url,
        path=path,
        method=http_verb,
        properties=properties,
        request_body=request_body,
    )


if __name__ == "__main__":
    CACHED_OPENAPI_SPECS = [
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/APIs/spotify.com/1.0.0/openapi.yaml",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/APIs/xkcd.com/1.0.0/openapi.yaml",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/APIs/notion.com/1.0.0/openapi.yaml",
        "https://raw.githubusercontent.com/APIs-guru/openapi-directory/main/APIs/twitter.com/current/2.61/openapi.yaml",
    ]
    results = []
    for spec in CACHED_OPENAPI_SPECS:
        spec_dict = get_spec_from_url(spec)
        full_components_dict = get_full_components_dict(spec_dict)
        for path in spec_dict.get("paths", {}).keys():
            for verb in HTTPVerb:
                res = parse_path(
                    spec_dict,
                    path,
                    verb.value,
                    full_components_dict=full_components_dict,
                )
        # res = extract_components(spec_dict)
        results.append(res)
        print(res)
