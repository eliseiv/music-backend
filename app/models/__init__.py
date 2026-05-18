from app.models.base import Base

# Music-module models register themselves with `Base.metadata` when imported.
# Alembic env.py imports `app.music.models` explicitly.

__all__ = ["Base"]
