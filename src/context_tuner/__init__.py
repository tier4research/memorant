"""Context Tuner v1 — adaptive context compression for LLM agent conversations.

Primary API: ContextTuner

When a conversation grows too large for the context window,
Context Tuner intelligently summarizes older messages while
preserving critical information. Includes SQLite-backed
recovery for rollback/decompression.

Package: tier4research/context-tuner
"""

from .core import ContextTuner, TunerConfig, CompressedMessages, CompressionDebug
from .recovery import RecoveryStore, RecoveryRecord
from .errors import RecoveryCorruptionError
from .compressor import (
    compress_messages,
    compress_messages_detailed,
    CompressionOutcome,
    count_tokens,
    count_message_tokens,
    extract_key_facts,
    chunk_messages,
    default_summarize_chunk,
)

__all__ = [
    "ContextTuner",
    "TunerConfig",
    "CompressedMessages",
    "CompressionDebug",
    "RecoveryStore",
    "RecoveryRecord",
    "RecoveryCorruptionError",
    "CompressionOutcome",
    "compress_messages",
    "compress_messages_detailed",
    "count_tokens",
    "count_message_tokens",
    "extract_key_facts",
    "chunk_messages",
    "default_summarize_chunk",
]
__version__ = "1.0.0-rc.1"
