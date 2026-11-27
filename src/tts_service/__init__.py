"""VieNeu-TTS Distributed Engine & Subtitle Sync Service."""

import os

# Disable gRPC C-core pthread_atfork handlers before grpc is imported:
# this service only spawns external binaries (ffmpeg, ffsubsync), never forked
# Python gRPC workers, so fork handlers only emit noisy fork_posix.cc logs.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

__version__ = "2.0.0"
