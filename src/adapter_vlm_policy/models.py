from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaSource(Strict):
    type: Literal["inline_base64", "file", "context_media"]
    mime_type: str | None = None
    data: str | None = None
    path: str | None = None
    media_id: str | None = None

    @model_validator(mode="after")
    def required_source(self):
        required = {
            "inline_base64": "data",
            "file": "path",
            "context_media": "media_id",
        }[self.type]
        if not getattr(self, required):
            raise ValueError(f"{self.type} requires {required}")
        return self


class Content(Strict):
    type: Literal["text", "image"]
    text: str | None = None
    image_id: str | None = None
    source: MediaSource | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_content(self):
        if self.type == "text" and self.text is None:
            raise ValueError("Text block requires text")
        if self.type == "image" and (not self.image_id or self.source is None):
            raise ValueError("Image block requires image_id/source")
        return self


class Message(Strict):
    role: Literal["system", "developer", "user", "assistant"]
    content: str | list[Content]


class Selection(Strict):
    record_ids: list[str] = Field(default_factory=list, max_length=32)


class ContextSpec(Strict):
    mode: Literal["stateless", "task"] = "stateless"
    task_id: str | None = Field(default=None, max_length=200)
    context_id: str | None = None
    branch_id: Literal["main"] = "main"
    expected_version: int | None = Field(default=None, ge=0)
    updates: dict[str, Any] = Field(default_factory=dict)
    selection: Selection = Field(default_factory=Selection)

    @model_validator(mode="after")
    def valid_context(self):
        if self.mode == "task":
            if not self.task_id or self.expected_version is None:
                raise ValueError("Task context requires task_id and expected_version")
        elif self.context_id or self.updates or self.selection.record_ids:
            raise ValueError("Stateless context cannot reference task state")
        return self


class ResponseFormat(Strict):
    type: Literal["text", "json_schema"] = "text"
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    schema_id: str | None = None

    @model_validator(mode="after")
    def valid_schema(self):
        if self.type == "json_schema" and (self.json_schema is None) == (self.schema_id is None):
            raise ValueError("JSON format requires exactly one of schema or schema_id")
        return self


class Limits(Strict):
    deadline_ms: int = Field(default=60000, ge=1)
    max_output_tokens: int = Field(default=1200, ge=1)


class InferenceRequest(Strict):
    schema_version: Literal["vlm.infer.request.v1"] = "vlm.infer.request.v1"
    request_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.:-]+$")
    messages: list[Message] = Field(min_length=1, max_length=100)
    context: ContextSpec = Field(default_factory=ContextSpec)
    response_format: ResponseFormat = Field(default_factory=ResponseFormat)
    model_route: str | None = None
    limits: Limits = Field(default_factory=Limits)
    metadata: dict[str, Any] = Field(default_factory=dict)
