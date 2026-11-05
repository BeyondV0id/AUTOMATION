"""Draft and send application emails, with a human approval gate.

The pipeline is deliberately split in two:

``draft``  renders a message from verified config and stores it as ``draft``.
           Nothing leaves the machine.
``send``   sends drafts that already exist. It needs ``--yes`` (or an
           interactive confirmation) and respects a daily cap.

Nothing here invents claims: every sentence comes from ``config/outreach.toml``
or from the posting itself.
"""

from .compose import OutreachConfig, compose, config_from, contact_for
from .store import DB, ensure_schema

__all__ = ["DB", "OutreachConfig", "compose", "config_from", "contact_for", "ensure_schema"]
