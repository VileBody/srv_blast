"""Season flow (Hooks S1) — onboarding, menu, referral tiers, phase routing.

Layered against the existing credit-product flow via `determine_flow()`. Free /
churned users go through this module; paid_active users continue in the legacy
flow untouched.
"""
from core.season_phase import PhaseStore, SeasonPhase, PhaseSnapshot
from .flow_router import determine_flow, parse_start_param
from .texts import (
    INTRO_1, INTRO_2, CONSENT, WELCOME,
    MENU_HEADER, render_generation_screen, render_about_season,
    render_invite_screen, render_pricing_screen, render_examples_screen,
    render_history_screen,
)
from .keyboards import (
    intro_next_kb, consent_kb, menu_kb, back_to_menu_kb,
    waitlist_kb, share_kb, notifications_kb, build_referral_link,
)
from .referral import SeasonReferralStore, QualificationResult, tier_for

__all__ = [
    "PhaseStore", "SeasonPhase", "PhaseSnapshot",
    "determine_flow", "parse_start_param",
    "INTRO_1", "INTRO_2", "CONSENT", "WELCOME",
    "MENU_HEADER", "render_generation_screen", "render_about_season",
    "render_invite_screen", "render_pricing_screen", "render_examples_screen",
    "render_history_screen",
    "intro_next_kb", "consent_kb", "menu_kb", "back_to_menu_kb",
    "waitlist_kb", "share_kb", "notifications_kb", "build_referral_link",
    "SeasonReferralStore", "QualificationResult", "tier_for",
]
