"""
Auth module for SpectreCodingAgent.
Mirrors SpectreInvestigationAgent pattern — reads LLM token from .auth.json locally,
or fetches SpectreRefreshToken asset on robot.
"""
import json
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AUTH_PATH = os.path.join(_PROJECT_ROOT, ".uipath", ".auth.json")
_TOKEN_URL = "https://staging.uipath.com/identity_/connect/token"
_CLIENT_ID = "36dea5b8-e8bb-423d-8e7b-c808df8f1c00"
_DEFAULT_BASE_URL = "https://staging.uipath.com/ad89db7f-af81-463f-865d-6c373f2feb96/ab8ad4cb-8820-42e7-a658-210ffaa23b75"
_ASSET_FOLDER_ID = "3087542"

_llm_token_cache: dict = {}


def _get_robot_token() -> str | None:
    return os.getenv("UIPATH_ACCESS_TOKEN") or os.getenv("UIPATH_ROBOT_ACCESS_TOKEN")


def _get_base_url() -> str:
    return os.getenv("UIPATH_URL", _DEFAULT_BASE_URL)


def get_llm_token() -> tuple[str, str]:
    """Return (access_token, base_url) for LLM gateway calls.
    Locally: reads .auth.json (has LLMGateway scope), refreshing if expired.
    On robot: fetches SpectreRefreshToken asset, exchanges for fresh JWT, writes new token back.
    """
    base_url = _get_base_url()

    if _llm_token_cache.get("access_token") and time.time() < _llm_token_cache.get("expires_at", 0):
        return _llm_token_cache["access_token"], base_url

    # On robot or locally with env var — use it directly (same pattern as InvestigationAgent)
    env_token = _get_robot_token()
    if env_token:
        return env_token, base_url

    if os.path.exists(_AUTH_PATH):
        with open(_AUTH_PATH) as f:
            data = json.load(f)
        issued_at = data.get("issued_at", time.time())
        expires_in = data.get("expires_in", 3600)
        if time.time() < issued_at + expires_in - 60:
            _llm_token_cache["access_token"] = data["access_token"]
            _llm_token_cache["expires_at"] = issued_at + expires_in - 60
            return _llm_token_cache["access_token"], base_url
        resp = requests.post(
            _TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": _CLIENT_ID},
            timeout=10,
        )
        resp.raise_for_status()
        new_data = resp.json()
        new_data["issued_at"] = time.time()
        data.update(new_data)
        with open(_AUTH_PATH, "w") as f:
            json.dump(data, f)
        _llm_token_cache["access_token"] = data["access_token"]
        _llm_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
        return _llm_token_cache["access_token"], base_url

    pat, _ = get_pat()
    headers = {"Authorization": f"Bearer {pat}", "X-UIPATH-OrganizationUnitId": _ASSET_FOLDER_ID}

    resp = requests.get(
        f"{base_url}/orchestrator_/odata/Assets?$filter=Name eq 'SpectreRefreshToken'",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    assets = resp.json().get("value", [])
    if not assets:
        raise ValueError("SpectreRefreshToken asset not found")
    refresh_token = assets[0].get("StringValue", "") or assets[0].get("Value", "")
    if not refresh_token:
        raise ValueError("SpectreRefreshToken value is empty")

    token_resp = requests.post(
        _TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": _CLIENT_ID},
        timeout=10,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    expires_in = token_data.get("expires_in", 3600)
    _llm_token_cache["access_token"] = token_data["access_token"]
    _llm_token_cache["expires_at"] = time.time() + expires_in - 60

    new_refresh_token = token_data.get("refresh_token")
    if new_refresh_token:
        asset_id = assets[0].get("Id")
        requests.put(
            f"{base_url}/orchestrator_/odata/Assets({asset_id})",
            headers=headers,
            json={"Id": asset_id, "Name": "SpectreRefreshToken", "ValueType": "Text", "StringValue": new_refresh_token},
            timeout=10,
        )

    return _llm_token_cache["access_token"], base_url


def get_pat() -> tuple[str, str]:
    """Return (PAT, base_url). Locally from UIPATH_PAT env var; on robot from SpectrePAT asset."""
    base_url = _get_base_url()
    pat = os.getenv("UIPATH_PAT")
    if pat:
        return pat, base_url

    robot_token = _get_robot_token()
    if not robot_token:
        raise ValueError("Neither UIPATH_PAT nor UIPATH_ACCESS_TOKEN is available")

    headers = {"Authorization": f"Bearer {robot_token}", "X-UIPATH-OrganizationUnitId": _ASSET_FOLDER_ID}
    resp = requests.get(
        f"{base_url}/orchestrator_/odata/Assets?$filter=Name eq 'SpectrePAT'",
        headers=headers,
        timeout=10,
    )
    if resp.ok:
        assets = resp.json().get("value", [])
        if assets:
            stored_pat = assets[0].get("StringValue", "") or assets[0].get("Value", "")
            if stored_pat:
                return stored_pat, base_url

    return robot_token, base_url
