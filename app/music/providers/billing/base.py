from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.music.enums import BillingEventKind, BillingProvider


@dataclass
class NormalizedBillingEvent:
    provider: BillingProvider
    event_id: str
    kind: BillingEventKind
    external_user_id: str
    product_external_id: str | None
    token_amount: int | None
    occurred_at: datetime
    expires_at: datetime | None
    payload_digest: str
    raw: dict[str, Any] = field(default_factory=dict)
