"""Strict, bounded service result contract. Unknown facts remain null."""
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator, field_validator
from .normalization import MAX_BYTES, MAX_SEGMENTS, MAX_TIME_MS, _segment, _text

class Segment(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    start_ms: Annotated[StrictInt, Field(ge=0, le=MAX_TIME_MS)] | None
    end_ms: Annotated[StrictInt, Field(ge=0, le=MAX_TIME_MS)] | None
    text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_BYTES)]
    speaker: Annotated[StrictStr, Field(min_length=1, max_length=512)] | None

    @model_validator(mode='after')
    def valid_segment(self):
        canonical = _segment(self.text, self.start_ms, self.end_ms, self.speaker)
        if canonical['text'] != self.text or canonical['speaker'] != self.speaker:
            raise ValueError('Noncanonical segment text')
        return self

class NormalizedTranscript(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    schema_version: Literal[1]
    format: Literal['json', 'canonical_json', 'srt', 'vtt']
    segments: Annotated[list[Segment], Field(min_length=1, max_length=MAX_SEGMENTS)]
    full_text: Annotated[StrictStr, Field(max_length=MAX_BYTES)]
    text: Annotated[StrictStr, Field(max_length=MAX_BYTES)]
    vtt: Annotated[StrictStr, Field(max_length=MAX_BYTES * 4)]
    warnings: Annotated[list[Annotated[StrictStr, Field(max_length=128)]], Field(max_length=32)]

    @field_validator('schema_version', mode='before')
    @classmethod
    def version_int(cls, value):
        if type(value) is not int:
            raise ValueError('Invalid schema version')
        return value

    @model_validator(mode='after')
    def coherent_projection(self):
        joined = '\n'.join(s.text for s in self.segments)
        if self.full_text != joined or self.text != joined:
            raise ValueError('Transcript text does not match segments')
        for value in [self.full_text, self.text, self.vtt, *self.warnings]:
            _text(value)
        if any(s.start_ms is None for s in self.segments) and self.vtt:
            raise ValueError('Unknown timestamps cannot produce VTT')
        return self

def validate_normalized(value) -> dict:
    result = NormalizedTranscript.model_validate(value).model_dump()
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_BYTES * 8:
        raise ValueError('Normalized result exceeds limit')
    return result
