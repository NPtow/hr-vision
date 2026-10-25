"""Bounded v1 wire models. References never contain URLs or credentials."""
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, AfterValidator, BeforeValidator, model_validator


def safe_ref(value: str) -> str:
    if any(ord(c) < 32 for c in value) or re.search(r"[a-z][a-z0-9+.-]*://|bearer\s", value, re.I):
        raise ValueError("unsafe_reference")
    return value


Ref = Annotated[str, Field(min_length=1, max_length=500), AfterValidator(safe_ref)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ChannelState = Literal["processing", "ready", "error", "not_recorded", "unsupported"]
TranscriptionState = Literal["not_requested", "awaiting_configuration", "queued", "running", "succeeded", "failed"]
OwnerKind = Literal["interview", "meeting"]
def version(value):
    if type(value) is not int or value != 1:
        raise ValueError("unsupported_schema_version")
    return value


Version = Annotated[Literal[1], BeforeValidator(version)]


class Model(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class BindingInput(Model):
    schema_version: Version
    owner_kind: OwnerKind
    owner_id: Ref
    provider: Ref
    connection_id: Ref
    source_ref: dict
    storage_policy: Literal["remote", "owned"]


class CollectInput(Model):
    storage_policy: Literal["remote", "owned"] | None = None


class Metadata(Model):
    duration: Annotated[float, Field(ge=0)] | None = None
    format: Ref | None = None
    session_id: Ref | None = None


class Location(Model):
    backend: Literal["provider", "s3"]
    object_key: Ref
    state: Literal["available", "verified"]
    size: Annotated[int, Field(ge=0)] | None = None
    sha256: Digest | None = None
    content_type: Ref | None = None

    @model_validator(mode="after")
    def verification(self):
        if self.backend == "s3" and (self.size is None or self.sha256 is None):
            raise ValueError("owned_location_requires_size_and_sha256")
        if self.backend == "provider" and self.state != "available":
            raise ValueError("provider_location_cannot_assert_verification")
        return self


class RawReference(Model):
    backend: Literal["s3"]
    object_key: Ref
    size: Annotated[int, Field(ge=0)]
    sha256: Digest
    content_type: Ref | None = None


class Segment(Model):
    text: Annotated[str, Field(min_length=1, max_length=8 * 1024 * 1024)]
    start_ms: Annotated[int, Field(ge=0)] | None
    end_ms: Annotated[int, Field(ge=0)] | None
    speaker: Annotated[str, Field(max_length=500)] | None

    @model_validator(mode="after")
    def times(self):
        if not self.text.strip() or (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("invalid_canonical_segment")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("invalid_segment_interval")
        return self


class NormalizedTranscript(Model):
    schema_version: Version
    format: Literal["vtt", "srt", "json", "canonical_json"]
    segments: Annotated[list[Segment], Field(min_length=1, max_length=100000)]
    full_text: Annotated[str, Field(max_length=8 * 1024 * 1024)]
    text: Annotated[str, Field(max_length=8 * 1024 * 1024)]
    vtt: Annotated[str, Field(max_length=20 * 1024 * 1024)]
    warnings: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=100)]

    @model_validator(mode="after")
    def consistent_text(self):
        if self.text != self.full_text or self.full_text != "\n".join(s.text for s in self.segments):
            raise ValueError("normalized_text_mismatch")
        return self


class Provenance(Model):
    origin: Ref
    model_version: Ref | None = None
    language: Ref | None = None
    processing_status: Ref
    coverage: Ref
    input_asset_id: Ref | None = None
    input_sha256: Digest | None = None

    @model_validator(mode="after")
    def input_reference(self):
        if (self.input_asset_id is None) != (self.input_sha256 is None):
            raise ValueError("incomplete_input_reference")
        return self


class Transcript(Model):
    raw_ref: RawReference
    normalized: NormalizedTranscript
    provenance: Provenance


class Asset(Model):
    external_id: Ref
    kind: Literal["video", "audio", "transcript"]
    state: ChannelState
    metadata: Metadata = Field(default_factory=Metadata)
    locations: Annotated[list[Location], Field(max_length=10)] = Field(default_factory=list)
    transcript: Transcript | None = None

    @model_validator(mode="after")
    def transcript_kind(self):
        if self.transcript is not None and self.kind != "transcript":
            raise ValueError("invalid_transcript_asset")
        if self.kind == "transcript" and self.state == "ready" and self.transcript is None:
            raise ValueError("ready_transcript_requires_normalized_content")
        if self.state == "ready" and self.kind in ("video", "audio") and not self.locations:
            raise ValueError("ready_media_requires_location")
        if len({(x.backend, x.object_key) for x in self.locations}) != len(self.locations):
            raise ValueError("duplicate_location")
        return self


class Receipt(Model):
    schema_version: Version
    generation: Annotated[int, Field(gt=0)]
    receipt_id: Ref
    assets: Annotated[list[Asset], Field(max_length=100)]
    video_status: ChannelState
    transcript_status: ChannelState
    acquisition_complete: bool
    transcription_status: TranscriptionState

    @model_validator(mode="after")
    def unique_assets(self):
        if len({(a.kind, a.external_id) for a in self.assets}) != len(self.assets):
            raise ValueError("duplicate_asset")
        return self


class TranscriptSelectionInput(Model):
    generation: Annotated[int, Field(gt=0)]
    request_id: Ref
    expected_revision: Annotated[int, Field(ge=0)]
    status: Literal["pending", "ready", "failed", "uncertain"]
    transcript_id: Ref | None = None
    error_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")] | None = None

    @model_validator(mode="after")
    def selection_state(self):
        if (self.status == "ready") != (self.transcript_id is not None):
            raise ValueError("selection_transcript_required_only_when_ready")
        if self.status in ("pending", "ready") and self.error_code is not None:
            raise ValueError("selection_error_not_allowed")
        return self
