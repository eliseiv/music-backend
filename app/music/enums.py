from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class JobStage(str, Enum):
    prepare_prompt = "prepare_prompt"
    lyrics = "lyrics"
    music_generation = "music_generation"
    audio_to_audio_refine = "audio_to_audio_refine"
    vocal_tts = "vocal_tts"
    mix_master = "mix_master"
    upload_cdn = "upload_cdn"
    finalize = "finalize"


class TokenLedgerKind(str, Enum):
    credit_purchase = "credit_purchase"
    credit_subscription_grant = "credit_subscription_grant"
    debit_reserve = "debit_reserve"
    debit_capture = "debit_capture"
    credit_release = "credit_release"
    credit_refund = "credit_refund"
    debit_adjustment = "debit_adjustment"
    credit_adjustment = "credit_adjustment"


class BillingMode(str, Enum):
    per_track = "per_track"
    per_minute = "per_minute"


class RoundingMode(str, Enum):
    ceil = "ceil"
    floor = "floor"
    nearest = "nearest"


class SubscriptionStatus(str, Enum):
    none = "none"
    active = "active"
    canceled = "canceled"
    expired = "expired"


class BillingProvider(str, Enum):
    adapty = "adapty"
    rustore = "rustore"


class BillingPlatform(str, Enum):
    adapty = "adapty"
    rustore = "rustore"


class BillingEventKind(str, Enum):
    subscription_purchased = "SUBSCRIPTION_PURCHASED"
    subscription_renewed = "SUBSCRIPTION_RENEWED"
    subscription_canceled = "SUBSCRIPTION_CANCELED"
    subscription_expired = "SUBSCRIPTION_EXPIRED"
    one_time_purchase = "ONE_TIME_PURCHASE"
    refund = "REFUND"


class BeatGenre(str, Enum):
    electronic_dance = "electronic_dance"
    rap = "rap"
    lofi = "lofi"
    global_groove = "global_groove"
    relaxing_meditation = "relaxing_meditation"


class SampleCategory(str, Enum):
    harmonic_bass = "harmonic_bass"
    harmonic_lead = "harmonic_lead"
    harmonic_chord = "harmonic_chord"
    drums_kick = "drums_kick"
    drums_snare = "drums_snare"
    drums_closed_hihat = "drums_closed_hihat"
    drums_open_hihat = "drums_open_hihat"
    drums_auxiliary = "drums_auxiliary"
    mixing = "mixing"
    sound_effects = "sound_effects"


class WebhookProvider(str, Enum):
    fal = "fal"
    adapty = "adapty"
    rustore = "rustore"
