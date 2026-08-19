#!/usr/bin/env python3
"""
live_deploy — custom_scripts/generate_vapid_keys.py

Generates ONE VAPID keypair for Web Push (mobile notifications) and
prints exactly what to put in config.json (or the equivalent env vars)
to turn the feature on. Run this ONCE, ever, per deployment of this
app — never regenerate afterward, since every existing push
subscription (every phone that tapped "Enable notifications") is tied
to this specific keypair; regenerating silently breaks notifications
for everyone already opted in until they re-subscribe.

Standalone, no dependencies beyond `cryptography` (already pulled in
transitively by `pywebpush`, itself already in requirements.txt) — does
NOT touch the database or need the app server running, same as every
other script in this folder (see custom_scripts/README.md).

USAGE:
    cd live_deploy
    python3 custom_scripts/generate_vapid_keys.py

Then either add the three printed lines to config.json, or set the
three printed environment variables (env vars win if both are set —
see app/config.py's own VAPID handling). vapid_subject is a contact
URL/email the push services use to reach you if they ever need to
(e.g. a "your integration is misbehaving" notice) — a mailto: address
is the simplest valid value and is NOT shown to end users anywhere.
"""

import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> int:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    # Raw uncompressed public key point (65 bytes: 0x04 + X + Y) -- what
    # the BROWSER needs as PushManager.subscribe()'s applicationServerKey.
    public_raw = public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    # Raw 32-byte private scalar -- what py_vapid.Vapid01.from_string
    # (used internally by pywebpush.webpush) expects for vapid_private_key.
    private_value = private_key.private_numbers().private_value
    private_raw = private_value.to_bytes(32, "big")

    public_b64 = _b64url(public_raw)
    private_b64 = _b64url(private_raw)

    print("Generated a new VAPID keypair for Web Push.\n")
    print("Add these three lines to config.json (or set the equivalent")
    print("VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT env vars —")
    print("see app/config.py's own VAPID handling for which wins if both are set):\n")
    print(f'  "vapid_public_key": "{public_b64}",')
    print(f'  "vapid_private_key": "{private_b64}",')
    print('  "vapid_subject": "mailto:you@example.com",')
    print("\n*** SAVE THE PRIVATE KEY SOMEWHERE SAFE. Regenerating this keypair")
    print("later silently breaks notifications for everyone already opted in")
    print("until they re-subscribe -- there is no way to recover a lost one. ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
