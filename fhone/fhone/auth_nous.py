"""Nous sign-in — the identity of FHONE, and the first thing it does.

This is the same flow Hermes uses (``hermes_cli/anon_auth.py``), reproduced with
the standard library. It is a device-code style flow:

  1. POST /api/anonymous/create          -> mint a guest identity
  2. POST /api/anonymous/token           -> exchange for short-lived JWTs
  3. POST /api/anonymous/promotion-intent -> get a claim_url for the user
  4. POST /api/anonymous/promotion-status -> poll until the claim completes

Steps 1-2 give free-tier inference with no account. Step 3 is the "official
notification": the user opens the URL, approves on the Nous site, and the device
is upgraded into their real account.

Two honest caveats, which is why every step has a typed error and a timeout:

  * These are Hermes' INTERNAL endpoints, not a published public API. They are as
    stable as `hermes login`, but they are not a contract for third parties.
  * Nothing here has been executed against the live service from this codebase.
    It is written from the source that Hermes itself uses.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config as config_mod
from .llm import USER_AGENT

ANON_CLIENT_ID = "nas-anonymous"
DEFAULT_PORTAL = "https://portal.nousresearch.com"


class NousError(RuntimeError):
    pass


def _post(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                import gzip
                raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read(600).decode("utf-8", "replace")
        raise NousError("HTTP %s: %s" % (e.code, " ".join(detail.split())[:200]))
    except urllib.error.URLError as e:
        raise NousError("network: %s" % e.reason)
    except ValueError as e:
        raise NousError("bad response: %s" % e)


def portal_base(cfg: dict) -> str:
    return (cfg.get("nous_portal_url") or DEFAULT_PORTAL).rstrip("/")


def mint_guest(cfg: dict) -> dict:
    """Step 1: create a guest identity. The token is shown once."""
    data = _post(portal_base(cfg) + "/api/anonymous/create", {})
    if not data.get("token"):
        raise NousError("no token in create response: %s" % str(data)[:200])
    return data


def exchange_token(cfg: dict, anon_token: str) -> dict:
    """Step 2: exchange the guest token for short-lived JWTs."""
    return _post(portal_base(cfg) + "/api/anonymous/token", {"token": anon_token})


def promotion_intent(cfg: dict, anon_token: str, user_code: str,
                     device_code: str) -> dict:
    """Step 3: register a promotion so a real account can claim this device."""
    return _post(portal_base(cfg) + "/api/anonymous/promotion-intent",
                 {"token": anon_token, "user_code": user_code,
                  "device_code": device_code})


def promotion_status(cfg: dict, claim_code: str) -> dict:
    """Step 4: poll. Returns the status object; 'completed' ends the wait."""
    return _post(portal_base(cfg) + "/api/anonymous/promotion-status",
                 {"claim_code": claim_code})


def sign_in(cfg: dict, ui=None, *, promote: bool | None = None,
            poll_seconds: int = 300) -> dict:
    """Full first-run flow. Returns the persisted auth dict.

    Never raises on the promotion path: a guest credential that works is a
    success, and a failed promotion must not block the agent from running.
    """
    auth = config_mod.load_auth()
    if auth.get("anon_token"):
        if ui:
            ui.note("  already signed in to Nous (guest identity present)")
        return auth

    if ui:
        ui.note("  minting a Nous guest identity…")
    minted = mint_guest(cfg)
    auth = {
        "auth_method": "anonymous",
        "client_id": ANON_CLIENT_ID,
        "anon_token": minted.get("token", ""),
        "user_id": minted.get("user_id"),
        "org_id": minted.get("org_id"),
        "idle_ttl_days": minted.get("idle_ttl_days"),
        "portal_base_url": portal_base(cfg),
        "created_at": int(time.time()),
    }

    if ui:
        ui.note("  exchanging for an inference token…")
    try:
        exchanged = exchange_token(cfg, auth["anon_token"])
        auth["inference_base_url"] = exchanged.get("inference_base_url")
        auth["access_token"] = exchanged.get("access_token")
        auth["expires_in"] = exchanged.get("expires_in")
    except NousError as e:
        if ui:
            ui.note("  ! token exchange failed (%s) — continuing as guest" % e)

    config_mod.save_auth(auth)
    if ui:
        ui.note("  guest identity stored in %s" % config_mod.auth_path())

    wants_promotion = cfg.get("nous_auto_promote", True) if promote is None else promote
    if wants_promotion:
        _try_promotion(cfg, auth, ui, poll_seconds)
    return auth


def _try_promotion(cfg: dict, auth: dict, ui, poll_seconds: int) -> None:
    """Steps 3-4. Every failure is reported, never fatal."""
    user_code = str(auth.get("user_code") or "")
    device_code = str(auth.get("device_code") or "")
    if not user_code or not device_code:
        # The device-code REQUEST step is not implemented in this build, so a
        # promotion cannot be registered. Saying so is better than POSTing empty
        # codes and reporting a confusing server error.
        if ui:
            ui.note("  ! promotion needs a device code, which this build does not "
                    "request yet.")
            ui.note("    FHONE keeps working on the guest tier. To use your own "
                    "Nous account, add a key instead:")
            ui.note("      fhone provider key nous <your-api-key>")
        return
    if ui:
        ui.note("  requesting a promotion claim so this device can join your account…")
    try:
        intent = promotion_intent(cfg, auth["anon_token"], user_code, device_code)
    except NousError as e:
        if ui:
            ui.note("  ! promotion unavailable (%s)" % e)
            ui.note("    FHONE keeps working on the guest tier.")
        return

    claim_url = intent.get("claim_url") or intent.get("verification_uri")
    claim_code = intent.get("claim_code") or intent.get("user_code")
    if not claim_url or not claim_code:
        if ui:
            ui.note("  ! promotion response was missing a claim URL; skipping")
        return

    if ui:
        ui.note("")
        ui.note("  " + "=" * 62)
        ui.note("  Open this URL and approve, to sign in to your Nous account:")
        ui.note("")
        ui.note("      " + claim_url)
        ui.note("")
        ui.note("  Ignore it and FHONE keeps running on the guest tier.")
        ui.note("  " + "=" * 62)

    interval = int(intent.get("interval") or 5)
    deadline = time.time() + min(int(intent.get("expires_in") or poll_seconds),
                                 poll_seconds)
    while time.time() < deadline:
        time.sleep(max(2, interval))
        try:
            status = promotion_status(cfg, claim_code)
        except NousError:
            continue
        state = str(status.get("status") or "").lower()
        if state in ("completed", "approved", "claimed"):
            auth["auth_method"] = "oauth_device_code"
            auth["promoted_at"] = int(time.time())
            for key in ("refresh_token", "access_token", "account_tier", "user_id"):
                if status.get(key):
                    auth[key] = status[key]
            config_mod.save_auth(auth)
            if ui:
                ui.note("  signed in to your Nous account. Welcome.")
            return
        if state in ("expired", "denied", "rejected"):
            if ui:
                ui.note("  promotion %s — staying on the guest tier" % state)
            return
    if ui:
        ui.note("  promotion not completed in time — staying on the guest tier")


def is_guest(cfg: dict) -> bool:
    auth = config_mod.load_auth()
    return auth.get("auth_method") == "anonymous"
