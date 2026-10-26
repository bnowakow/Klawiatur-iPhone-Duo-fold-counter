"""Extensible, side-effect-free command routing for a future chat reader.

No commands are enabled by default. Transport, persistence and cooldowns belong
in the future reader/worker; handlers only build optional reply text.
"""
from dataclasses import dataclass
import hashlib
from typing import Callable
from youtube_chat import validate_message


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    chat_id: str
    author_channel_id: str
    text: str


@dataclass(frozen=True)
class Reply:
    text: str
    request_key: str
    source_kind: str
    source_id: str
    chat_id: str


class CommandRegistry:
    def __init__(self):
        self.handlers: dict[str, Callable[[IncomingMessage, str], str | None]] = {}

    def register(self, command, handler):
        if (not command.startswith('!') or len(command)<2 or command != command.casefold()
                or any(c.isspace() for c in command) or command in self.handlers):
            raise ValueError('Command must be unique, lowercase and start with !')
        self.handlers[command] = handler

    def dispatch(self, message: IncomingMessage, bot_channel_id: str):
        if not bot_channel_id or message.author_channel_id == bot_channel_id:
            return None
        if not message.message_id or not message.chat_id or not message.author_channel_id:
            return None
        parts = message.text.strip().split(maxsplit=1)
        handler = self.handlers.get(parts[0].casefold()) if parts else None
        if handler is None:
            return None
        text = handler(message, parts[1] if len(parts)>1 else '')
        if text is None:
            return None
        validate_message(text)
        digest = hashlib.sha256((message.chat_id+'\0'+message.message_id).encode()).hexdigest()
        return Reply(text, 'command:'+digest, 'command', message.message_id, message.chat_id)
