"""Mobile-class detection for the bottom-nav feature.

We deliberately treat TABLETS as non-mobile: an iPad in landscape has the
screen real estate for the existing desktop hamburger and avoids surprising
existing users. Flip IS_MOBILE_TABLETS = True if you want iPad users to see
the bottom nav.

user-agents 2.2.0 (confirmed via pip show in the beaverhabits venv).
"""
from functools import lru_cache

from user_agents import parse

IS_MOBILE_TABLETS = False  # see module docstring


@lru_cache(maxsize=256)
def is_mobile(user_agent_string: str) -> bool:
    """True iff the User-Agent is a phone-class device.

    Cached because the same UA appears on every page request from a given
    client within a session. Parse cost is ~0.1ms but unnecessary to repeat.
    """
    if not user_agent_string:
        return False
    ua = parse(user_agent_string)
    if not ua.is_mobile:
        return False
    if ua.is_tablet and not IS_MOBILE_TABLETS:
        return False
    return True
