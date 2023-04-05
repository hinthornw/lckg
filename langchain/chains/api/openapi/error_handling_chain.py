# """Error handling chain."""

# import json
# import re
# from typing import Union

# import json5
# from openapi_schema_pydantic import OpenAPI, Schema

# from langchain import LLMChain, PromptTemplate
# from langchain.chains.api.openapi.typescript_utils import schema_to_typescript
# from langchain.llms import BaseLLM
# from langchain.schema import BaseOutputParser

# request_template = """You are a helpful AI Assistant.
# A user has instructed as follows: "{instructions}"

# You attempted to call the API with theh following schema:

# API_SCHEMA: {schema}

# You called this schema with the following arguments:
# PREVIOUS_ARGS: ```json
# {previous_args}
# ```

# This led an error:
# {error}

# You have two options:

# Retry calling by updating the call. If so, reply with the following format:

# ARGS: ```json
# {{valid json conforming to API_SCHEMA}}
# ```

# Otherwise, you can message the user with the following format:
# Message: ```text
# "The response"
# ```


# You have made {prev_attempts} attempts so far and shouldn't make more than 2.

# Begin
# -----

# """


# class APIErrorHandlerOutputParser(BaseOutputParser):
#     """Parse the error and decide whether to retry."""

#     def parse(self, llm_output: str) -> Union[dict, str]:
#         """Parse the request and error tags."""
#         json_match = re.search(r"```json(.*?)```", llm_output, re.DOTALL)
#         if json_match:
#             typescript_block = json_match.group(1).strip()
#             try:
#                 return json.dumps(json5.loads(typescript_block))
#             except json5.JSONDecodeError:
#                 return "ERROR: serializing request"
#         # Search for Message
#         message_match = re.search(r"```text(.*?)```", llm_output, re.DOTALL)
#         if message_match:
#             return message_match.group(1).strip()
#         else:
#             return "ERROR: making request"


# class APIRequesterChain(LLMChain):
#     """Get the request parser."""

#     @classmethod
#     def from_llm_and_typescript(
#         cls, llm: BaseLLM, typescript_definition: str, verbose: bool = True
#     ) -> LLMChain:
#         """Get the request parser."""
#         output_parser = APIErrorHandlerOutputParser()
#         prompt = PromptTemplate(
#             template=request_template,
#             output_parser=output_parser,
#             partial_variables={"schema": typescript_definition},
#             input_variables=["instructions", "previous_args", "error"],
#         )
#         return cls(prompt=prompt, llm=llm, verbose=verbose)

#     @classmethod
#     def from_operation_schema(
#         cls,
#         llm: BaseLLM,
#         operation_schema: Schema,
#         full_spec: OpenAPI,
#         verbose: bool = True,
#     ) -> "APIRequesterChain":
#         """Get the request parser."""
#         typescript_def = schema_to_typescript(
#             operation_schema, full_spec, verbose=verbose
#         )
#         return cls.from_llm_and_typescript(
#             llm=llm, typescript_definition=typescript_def, verbose=verbose
#         )
