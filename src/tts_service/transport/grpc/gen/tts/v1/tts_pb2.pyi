from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Language(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LANGUAGE_UNSPECIFIED: _ClassVar[Language]
    LANGUAGE_VI: _ClassVar[Language]
    LANGUAGE_EN: _ClassVar[Language]

class VoiceRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    VOICE_ROLE_UNSPECIFIED: _ClassVar[VoiceRole]
    VOICE_ROLE_A: _ClassVar[VoiceRole]
    VOICE_ROLE_B: _ClassVar[VoiceRole]

class ReferenceType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REFERENCE_TYPE_UNSPECIFIED: _ClassVar[ReferenceType]
    REFERENCE_TYPE_SUB: _ClassVar[ReferenceType]
    REFERENCE_TYPE_AUDIO: _ClassVar[ReferenceType]
LANGUAGE_UNSPECIFIED: Language
LANGUAGE_VI: Language
LANGUAGE_EN: Language
VOICE_ROLE_UNSPECIFIED: VoiceRole
VOICE_ROLE_A: VoiceRole
VOICE_ROLE_B: VoiceRole
REFERENCE_TYPE_UNSPECIFIED: ReferenceType
REFERENCE_TYPE_SUB: ReferenceType
REFERENCE_TYPE_AUDIO: ReferenceType

class SynthesizeRequest(_message.Message):
    __slots__ = ("text", "lang", "role", "speed")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    LANG_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    SPEED_FIELD_NUMBER: _ClassVar[int]
    text: str
    lang: Language
    role: VoiceRole
    speed: float
    def __init__(self, text: _Optional[str] = ..., lang: _Optional[_Union[Language, str]] = ..., role: _Optional[_Union[VoiceRole, str]] = ..., speed: _Optional[float] = ...) -> None: ...

class SynthesizeResponse(_message.Message):
    __slots__ = ("audio_wav", "sample_rate", "duration_seconds")
    AUDIO_WAV_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    audio_wav: bytes
    sample_rate: int
    duration_seconds: float
    def __init__(self, audio_wav: _Optional[bytes] = ..., sample_rate: _Optional[int] = ..., duration_seconds: _Optional[float] = ...) -> None: ...

class AudioChunk(_message.Message):
    __slots__ = ("pcm_data", "sample_rate", "is_final")
    PCM_DATA_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_FIELD_NUMBER: _ClassVar[int]
    IS_FINAL_FIELD_NUMBER: _ClassVar[int]
    pcm_data: bytes
    sample_rate: int
    is_final: bool
    def __init__(self, pcm_data: _Optional[bytes] = ..., sample_rate: _Optional[int] = ..., is_final: _Optional[bool] = ...) -> None: ...

class SyncSubtitleRequest(_message.Message):
    __slots__ = ("target_srt", "reference_data", "ref_type")
    TARGET_SRT_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_DATA_FIELD_NUMBER: _ClassVar[int]
    REF_TYPE_FIELD_NUMBER: _ClassVar[int]
    target_srt: bytes
    reference_data: bytes
    ref_type: ReferenceType
    def __init__(self, target_srt: _Optional[bytes] = ..., reference_data: _Optional[bytes] = ..., ref_type: _Optional[_Union[ReferenceType, str]] = ...) -> None: ...

class SyncSubtitleMetadata(_message.Message):
    __slots__ = ("ref_type",)
    REF_TYPE_FIELD_NUMBER: _ClassVar[int]
    ref_type: ReferenceType
    def __init__(self, ref_type: _Optional[_Union[ReferenceType, str]] = ...) -> None: ...

class SyncSubtitleChunk(_message.Message):
    __slots__ = ("metadata", "target_srt_chunk", "reference_chunk")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    TARGET_SRT_CHUNK_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_CHUNK_FIELD_NUMBER: _ClassVar[int]
    metadata: SyncSubtitleMetadata
    target_srt_chunk: bytes
    reference_chunk: bytes
    def __init__(self, metadata: _Optional[_Union[SyncSubtitleMetadata, _Mapping]] = ..., target_srt_chunk: _Optional[bytes] = ..., reference_chunk: _Optional[bytes] = ...) -> None: ...

class SyncSubtitleResponse(_message.Message):
    __slots__ = ("synced_srt",)
    SYNCED_SRT_FIELD_NUMBER: _ClassVar[int]
    synced_srt: bytes
    def __init__(self, synced_srt: _Optional[bytes] = ...) -> None: ...
