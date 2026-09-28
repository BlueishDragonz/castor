"""A minimal software WebAuthn authenticator for tests.

There is no real authenticator in CI, and the existing passkey tests only
ever exercised the surrounding plumbing: they seeded credential rows
directly and asserted on the list/delete endpoints. Nothing in the suite
ever called `verify_registration_response` or
`verify_authentication_response` with a genuine attestation or assertion,
so the security-critical server-side code — the part that decides whether
a passkey is real — had no coverage at all.

This module implements just enough of the FIDO2 authenticator model to
produce valid responses using the `cryptography` package that py-webauthn
already depends on:

  * ES256 (P-256) key pairs generated per credential
  * CBOR attestation objects (the "none" format, fmt "none")
  * Authenticator data with the correct RP ID hash, flags and counter
  * ECDSA-SHA256 assertions signed with the credential private key

Attestation is deliberately "none": the server requests
AttestationConveyancePreference.NONE, so a self-attestation is what a
real platform authenticator sends too. This is not a shortcut around
verification — the server still verifies the challenge, the RP ID hash,
the origin, the signature over authData||clientDataHash, and the counter
monotonicity. It is a real authenticator, just a software one.
"""
import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

# WebAuthn flag bits
FLAG_UP = 0x01  # user present
FLAG_UV = 0x04  # user verified
FLAG_BE = 0x08  # backup eligible
FLAG_BS = 0x10  # backup state


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class SoftwareAuthenticator:
    """A single-credential software authenticator."""

    def __init__(self, rp_id: str, origin: str, user_verified: bool = True):
        self.rp_id = rp_id
        self.origin = origin
        self.user_verified = user_verified
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.aaguid = os.urandom(16)

    # ── internals ──────────────────────────────────────────────────────
    @property
    def public_key_der(self) -> bytes:
        """Uncompressed SEC1 point, the form WebAuthn stores."""
        return self._key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )

    def _flags(self) -> int:
        flags = FLAG_UP
        if self.user_verified:
            flags |= FLAG_UV
        return flags

    def _auth_data(self, include_attested: bool = False) -> bytes:
        rp_id_hash = hashlib.sha256(self.rp_id.encode()).digest()
        flags = self._flags()
        if include_attested:
            flags |= 0x40  # AT
        counter = self.sign_count
        out = rp_id_hash + struct.pack(">BI", flags, counter)
        if include_attested:
            # The 16-byte AAGUID is a FIXED-WIDTH field, not a
            # length-prefixed one. Leaving it out shifts every subsequent
            # byte, and the COSE key then fails to decode with the deeply
            # misleading "premature end of stream" from CBOR.
            out += self.aaguid
            cred_id = self.credential_id
            out += struct.pack(">H", len(cred_id)) + cred_id
            # COSE key for ES256: kty=EC2(2), alg=ES256(-7), crv=P-256(1),
            # x and y as 32-byte bstrs.
            x = self._key.public_key().public_numbers().x
            y = self._key.public_key().public_numbers().y
            cose_key = {
                1: 2,   # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: x.to_bytes(32, "big"),
                -3: y.to_bytes(32, "big"),
            }
            cose = cbor2.dumps(cose_key)
            out += cose
        return out

    def _client_data(self, challenge_b64: str, origin: str | None = None) -> bytes:
        payload = {
            "type": "webauthn.create",
            "challenge": challenge_b64,
            "origin": origin or self.origin,
            "crossOrigin": False,
        }
        return json.dumps(payload).encode()

    # ── ceremonies ─────────────────────────────────────────────────────
    def register(self, options: dict) -> dict:
        """Produce a credential-creation response for `options`.

        `options` is the JSON the server returned. py-webauthn 3.x emits
        the registration options flat (no "publicKey" wrapper), so accept
        either shape rather than assuming the browser-style nesting.
        """
        pk = options["publicKey"] if "publicKey" in options else options
        challenge = pk["challenge"]
        # Must be set BEFORE _auth_data(), which packs the counter — a
        # fresh authenticator starts its signature counter at 1.
        self.sign_count = 1
        client_data = self._client_data(challenge)
        auth_data = self._auth_data(include_attested=True)
        # The attestation object is a CBOR map whose `authData` is a
        # BYTE STRING holding the authenticator data — not authData
        # concatenated with the statement. Building it as raw concatenation
        # makes the whole blob fail to decode, which surfaces as the
        # misleading "attestationObject was not a dict".
        att_obj = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": auth_data}
        )
        return {
            "id": b64u(self.credential_id),
            "rawId": b64u(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client_data),
                "attestationObject": b64u(att_obj),
            },
        }

    def authenticate(self, challenge_b64: str) -> dict:
        """Produce an assertion for `challenge_b64`."""
        self.sign_count += 1
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": challenge_b64,
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()
        auth_data = self._auth_data(include_attested=False)
        signature = self._key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": b64u(self.credential_id),
            "rawId": b64u(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(client_data),
                "authenticatorData": b64u(auth_data),
                "signature": b64u(signature),
                "userHandle": None,
            },
        }
