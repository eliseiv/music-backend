from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.search_request import SearchRequest

# Music-module models live in `app.music.models` and register themselves with
# `Base.metadata` when imported. Alembic env.py imports them explicitly; runtime
# code imports them on demand. Importing them here would create a circular
# dependency with `app/music/models/*` → `app/models/base`.

__all__ = ["Base", "Conversation", "Message", "SearchRequest"]
