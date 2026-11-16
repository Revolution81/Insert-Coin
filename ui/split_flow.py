"""
Split backups (SLIP39 shares). Writing and checking the shares, and typing
them back in, all happen on the signer's own screen: see split() and
shares() in ui/secure_flows.py.
"""
from __future__ import annotations


def split_backup(app):
    """SETUP > SPLIT BACKUP."""
    app.prompt("split_prompt", lambda app, _: None)
