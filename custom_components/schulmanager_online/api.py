"""Thin client for the (unofficial) Schulmanager Online API.

Reverse engineered from the production web app (Angular bundle). The login
sequence and the batched RPC endpoint are documented in ``docs/api.md``.

Keep every HTTP call in here so the rest of the integration stays testable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, unquote, urlencode

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    CALENDAR_DAYS_AFTER,
    CALENDAR_DAYS_BEFORE,
    CLASSBOOK_STATISTIC_TYPE,
    TIMETABLE_DAYS_AFTER,
    TIMETABLE_DAYS_BEFORE,
)
from .model import students_of

_LOGGER = logging.getLogger(__name__)

SITE_BASE = "https://login.schulmanager-online.de"
API_BASE = f"{SITE_BASE}/api"

# The web app sends the hash of its own JS bundle. The server only checks that
# the field is a non-empty string, but it is sent to stay close to the client.
BUNDLE_VERSION = "50535480bb"

# PBKDF2 parameters taken verbatim from the web app's hashing helper.
HASH_ITERATIONS = 99999
HASH_LENGTH = 512
HASH_ALGORITHM = "sha512"


class SchulmanagerError(Exception):
    """Generic error talking to Schulmanager Online."""


class SchulmanagerAuthError(SchulmanagerError):
    """Credentials were rejected."""


class SchulmanagerTwoFactorRequired(SchulmanagerError):
    """The account is protected by a second factor."""

    def __init__(self, method: str) -> None:
        """Remember whether an emailed code or a TOTP is expected."""
        super().__init__(f"Two-factor authentication required ({method})")
        self.method = method


class SchulmanagerMultipleAccounts(SchulmanagerError):
    """The email belongs to accounts at several schools."""

    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        """Remember the accounts so the caller can offer a choice."""
        super().__init__("Email is registered at multiple schools")
        self.accounts = accounts


# The web app shows a single sign-on button only when the matching probe returns
# a non-null value for the school. All four probes work unauthenticated.
SSO_PROBES: dict[str, str] = {
    "oidc": "get-sso-provider",
    "id-broker": "get-id-broker-kc-idp-hint",
    "logodidact": "get-logodidact-school-id",
    "vidis": "get-vidis-idp-hint",
}

# Human readable names for the providers the probes can report.
SSO_PROVIDER_LABELS: dict[str, str] = {
    "iserv": "IServ",
    "office365": "Office 365",
    "stadtkoeln": "Stadt Köln",
}

# The mobile app appends the user device to the final redirect as a fragment.
USER_DEVICE_FRAGMENT = re.compile(r"#userdevice=(.+)$")


@dataclass(frozen=True)
class SsoMethod:
    """One single sign-on option a school offers."""

    kind: str
    """One of the keys of :data:`SSO_PROBES`."""

    value: str
    """The provider hint or id the probe returned."""

    institution_id: int

    @property
    def label(self) -> str:
        """Return something worth showing in a menu."""
        pretty = SSO_PROVIDER_LABELS.get(self.value, self.value)
        if self.kind == "id-broker":
            return f"ID-Broker ({pretty})"
        if self.kind == "oidc":
            return f"Single sign-on with {pretty}"
        if self.kind == "logodidact":
            return f"logoDIDACT ({pretty})"
        return f"VIDIS ({pretty})"

    @property
    def url(self) -> str:
        """Build the browser URL that starts this flow.

        ``mobile-app=true`` makes the server finish the flow by redirecting to a
        URL carrying ``#userdevice=<json>`` instead of dropping the browser into
        the web app, which is the only variant a non-browser client can complete.
        """
        if self.kind == "id-broker":
            query = urlencode({"kc_idp_hint": self.value, "mobile-app": "true"})
            return f"{SITE_BASE}/id-broker?{query}"
        if self.kind == "oidc":
            query = urlencode({"mobile-app": "true"})
            return f"{SITE_BASE}/oidc/{self.institution_id}?{query}"
        if self.kind == "logodidact":
            query = urlencode({"mobile-app": "true"})
            school = quote(self.value, safe="")
            return f"{SITE_BASE}/logodidact-sso/{school}/-?{query}"
        query = urlencode(
            {
                "vidis_idp_hint": self.value,
                "institution_id": self.institution_id,
                "mobile-app": "true",
            }
        )
        return f"{SITE_BASE}/sso/vidis?{query}"


def parse_user_device(redirect_url: str) -> dict[str, Any]:
    """Pull the user device out of the final single sign-on redirect URL.

    The mobile app watches its in-app browser for a URL ending in
    ``#userdevice=<url-encoded JSON>`` and this is the same payload.
    """
    match = USER_DEVICE_FRAGMENT.search(redirect_url.strip())
    if match is None:
        raise SchulmanagerAuthError(
            "That URL carries no '#userdevice=' fragment — "
            "make sure to copy the address the browser ends up on"
        )
    try:
        device = json.loads(unquote(match.group(1)))
    except ValueError as err:
        raise SchulmanagerAuthError(f"Could not decode the user device: {err}") from err
    if not isinstance(device, dict):
        raise SchulmanagerAuthError("The user device fragment is not an object")
    return device


def hash_password(password: str, salt: str) -> str:
    """Derive the login hash the way the web app does.

    The browser turns the password into bytes with ``charCodeAt`` written into a
    ``Uint8Array``, which truncates every UTF-16 code unit to its low byte. That
    differs from UTF-8 for non-ASCII passwords, so it is reproduced exactly. The
    salt, in contrast, is UTF-8 encoded (``TextEncoder``).
    """
    password_bytes = bytes(ord(char) & 0xFF for char in password)
    derived = hashlib.pbkdf2_hmac(
        HASH_ALGORITHM,
        password_bytes,
        salt.encode("utf-8"),
        HASH_ITERATIONS,
        dklen=HASH_LENGTH,
    )
    return derived.hex()


class SchulmanagerClient:
    """Minimal async API client."""

    def __init__(
        self,
        session: ClientSession,
        email: str,
        password: str,
        institution_id: int | None = None,
    ) -> None:
        """Store credentials and the shared aiohttp session."""
        self._session = session
        self._email = email
        self._password = password
        self._institution_id = institution_id
        self._token: str | None = None
        self.user: dict[str, Any] | None = None
        self.user_device: dict[str, Any] | None = None

    @property
    def token(self) -> str | None:
        """Return the cached bearer token."""
        return self._token

    def set_credentials(self, email: str, password: str) -> None:
        """Replace the stored credentials, e.g. after an interactive prompt."""
        self._email = email
        self._password = password

    @property
    def institution_id(self) -> int | None:
        """Return the institution the client logs in against."""
        return self._institution_id

    @institution_id.setter
    def institution_id(self, value: int | None) -> None:
        """Pin the institution, e.g. after a ``multipleAccounts`` response."""
        self._institution_id = value

    async def _post(
        self, path: str, payload: dict[str, Any], *, authenticated: bool = False
    ) -> Any:
        """POST JSON to the API and return the decoded body."""
        headers = {"Accept": "application/json"}
        if self.user_device:
            headers["X-Token"] = json.dumps(
                {"id": self.user_device.get("id"), "key": self.user_device.get("key")}
            )
        if authenticated:
            if self._token is None:
                raise SchulmanagerAuthError("Not logged in")
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            async with self._session.post(
                f"{API_BASE}/{path}", json=payload, headers=headers
            ) as response:
                return await self._handle_response(response)
        except ClientError as err:
            raise SchulmanagerError(f"Request to {path} failed: {err}") from err

    async def _handle_response(self, response: ClientResponse) -> Any:
        """Validate the status code, roll the token and decode the body."""
        # The server hands out a fresh token on every authenticated call.
        if new_token := response.headers.get("x-new-bearer-token"):
            self._token = new_token

        if response.status == 401:
            raise SchulmanagerAuthError(f"Credentials rejected by {response.url.path}")
        if response.status == 403:
            body = await _safe_json(response)
            if isinstance(body, dict) and body.get("untrustedNetwork"):
                raise SchulmanagerAuthError("Login blocked from this network")
            raise SchulmanagerError("Login forbidden")
        if response.status == 429:
            raise SchulmanagerError("Rate limited by Schulmanager Online")
        if response.status >= 400:
            raise SchulmanagerError(f"HTTP {response.status} from {response.url.path}")

        return await response.json(content_type=None)

    async def async_find_schools(self, query: str) -> list[dict[str, Any]]:
        """Search the public school directory. Needs no authentication."""
        return await self.async_call("find-schools", {"query": query})

    async def async_get_sso_methods(self, institution_id: int) -> list[SsoMethod]:
        """Return the single sign-on options a school offers.

        Mirrors what the login page resolves before deciding which buttons to
        render: one probe per provider, each returning a hint or ``None``. Needs
        no authentication.
        """
        endpoints = list(SSO_PROBES.items())
        results = await self.async_call_batch(
            [
                {
                    "moduleName": None,
                    "endpointName": endpoint,
                    "parameters": {"institutionId": institution_id},
                }
                for _, endpoint in endpoints
            ]
        )
        return [
            SsoMethod(kind=kind, value=value, institution_id=institution_id)
            for (kind, _), value in zip(endpoints, results, strict=True)
            if isinstance(value, str) and value
        ]

    async def async_login_with_user_device(
        self, device: dict[str, Any], reason: str = "ha-integration"
    ) -> None:
        """Exchange a stored user device for a fresh token.

        This is how the mobile app resumes a session, and the only way to finish
        a single sign-on flow without being a browser. The device is long lived,
        so storing it beats storing a password.
        """
        result = await self._post(
            "login-with-user-device", {"device": device, "reason": reason}
        )
        token = result.get("jwt")
        if not token:
            raise SchulmanagerAuthError("Device login returned no token")
        self._token = token
        self.user = result.get("user")
        self.user_device = result.get("userDevice") or device

    async def async_login(self, two_factor_code: str | None = None) -> None:
        """Authenticate and cache the bearer token."""
        salt = await self._async_get_salt()
        password_hash = hash_password(self._password, salt) if salt else None

        result = await self._post(
            "login",
            {
                "emailOrUsername": self._email,
                "password": self._password,
                "hash": password_hash,
                "mobileApp": False,
                "userId": None,
                "twoFactorCode": two_factor_code,
                "institutionId": self._institution_id,
            },
        )

        if accounts := result.get("multipleAccounts"):
            raise SchulmanagerMultipleAccounts(accounts)
        if result.get("requireTwoFactorEmailCode"):
            raise SchulmanagerTwoFactorRequired("email")
        if result.get("requireTOTP"):
            raise SchulmanagerTwoFactorRequired("totp")

        token = result.get("jwt")
        if not token:
            raise SchulmanagerAuthError("Login response contained no token")

        self._token = token
        self.user = result.get("user")
        self.user_device = result.get("userDevice")

    async def _async_get_salt(self) -> str | None:
        """Fetch the per-account salt, or None if the server refuses one."""
        try:
            salt = await self._post(
                "get-salt",
                {
                    "emailOrUsername": self._email,
                    "userId": None,
                    "institutionId": self._institution_id,
                },
            )
        except SchulmanagerError as err:
            # The web app also tolerates a missing salt and falls back to
            # sending the plain password only.
            _LOGGER.debug("Could not fetch salt, continuing without hash: %s", err)
            return None
        return salt if isinstance(salt, str) else None

    async def async_call(
        self,
        endpoint: str,
        parameters: dict[str, Any] | None = None,
        module: str | None = None,
    ) -> Any:
        """Invoke a single RPC endpoint and return its ``data`` payload."""
        results = await self.async_call_batch(
            [{"moduleName": module, "endpointName": endpoint, "parameters": parameters}]
        )
        return results[0]

    async def async_call_batch(self, requests: list[dict[str, Any]]) -> list[Any]:
        """Invoke several RPC endpoints in one round trip."""
        body = await self._post(
            "calls",
            {"bundleVersion": BUNDLE_VERSION, "requests": requests},
            authenticated=self._token is not None,
        )

        data: list[Any] = []
        for request, result in zip(requests, body["results"], strict=True):
            endpoint = request["endpointName"]
            status = result.get("status")
            if status == 401:
                raise SchulmanagerAuthError(f"Token rejected calling {endpoint}")
            if status != 200:
                message = (result.get("userError") or {}).get(
                    "germanErrorMessage", f"status {status}"
                )
                raise SchulmanagerError(f"Call to {endpoint} failed: {message}")
            data.append(result.get("data"))
        return data

    async def async_call_batch_lenient(
        self, requests: list[dict[str, Any]]
    ) -> list[Any]:
        """Like :meth:`async_call_batch` but tolerate individual failures.

        Schools license modules separately, so asking a school without the
        homework module for homework is an expected 400, not an outage. A 401
        still propagates because that means the whole session is done.
        """
        body = await self._post(
            "calls",
            {"bundleVersion": BUNDLE_VERSION, "requests": requests},
            authenticated=self._token is not None,
        )

        data: list[Any] = []
        for request, result in zip(requests, body["results"], strict=True):
            status = result.get("status")
            if status == 401:
                raise SchulmanagerAuthError(
                    f"Token rejected calling {request['endpointName']}"
                )
            if status != 200:
                message = (result.get("userError") or {}).get(
                    "germanErrorMessage", f"status {status}"
                )
                _LOGGER.debug("Skipping %s: %s", request["endpointName"], message)
                data.append(None)
                continue
            data.append(result.get("data"))
        return data

    async def async_ensure_login(self) -> None:
        """Log in if there is no usable token yet.

        Prefers the stored user device, which works for single sign-on accounts
        and avoids sending the password again.
        """
        if self._token is not None:
            return
        if self.user_device:
            try:
                await self.async_login_with_user_device(self.user_device)
                return
            except SchulmanagerAuthError:
                _LOGGER.debug("Stored user device rejected, falling back to password")
                self.user_device = None
        if not self._email or not self._password:
            raise SchulmanagerAuthError("No usable credentials")
        await self.async_login()

    async def async_get_data(self) -> dict[str, Any]:
        """Fetch everything the coordinator needs.

        One batched round trip for the account-wide payloads, then one per
        student. Retries once after re-authenticating, because a token that
        expired between polls is routine rather than an error worth surfacing.
        """
        try:
            return await self._async_fetch()
        except SchulmanagerAuthError:
            _LOGGER.debug("Session expired, logging in again")
            self._token = None
            await self.async_ensure_login()
            return await self._async_fetch()

    async def _async_fetch(self) -> dict[str, Any]:
        """Do the actual fetching, assuming a valid session."""
        await self.async_ensure_login()

        today = date.today()
        start = (today - timedelta(days=TIMETABLE_DAYS_BEFORE)).isoformat()
        end = (today + timedelta(days=TIMETABLE_DAYS_AFTER)).isoformat()

        students = students_of(self.user)
        if not students:
            raise SchulmanagerError(
                "This account has no associated student — teacher and "
                "administrator logins are not supported"
            )

        calendar_start = (today - timedelta(days=CALENDAR_DAYS_BEFORE)).isoformat()
        calendar_end = (today + timedelta(days=CALENDAR_DAYS_AFTER)).isoformat()

        shared = [
            {"moduleName": None, "endpointName": "get-class-hours", "parameters": {}},
            {"moduleName": None, "endpointName": "get-letters", "parameters": {}},
            {
                "moduleName": "calendar",
                "endpointName": "get-events-for-user",
                "parameters": {
                    "start": calendar_start,
                    "end": calendar_end,
                    "includeHolidays": True,
                },
            },
            {
                "moduleName": "calendar",
                "endpointName": "get-event-categories",
                "parameters": {},
            },
            {
                "moduleName": "classbook",
                "endpointName": "get-current-term",
                "parameters": {},
            },
        ]
        shared_results = await self.async_call_batch_lenient(shared)
        class_hours_raw, letters, events, categories, term = shared_results

        term = term if isinstance(term, dict) else {}
        term_start = term.get("start") or start
        term_id = term.get("id")

        per_student = [
            request
            for student in students
            for request in (
                {
                    "moduleName": "schedules",
                    "endpointName": "get-actual-lessons",
                    "parameters": {
                        "student": {"id": student["id"]},
                        "start": start,
                        "end": end,
                    },
                },
                {
                    "moduleName": "exams",
                    "endpointName": "get-exams",
                    "parameters": {
                        "student": {"id": student["id"]},
                        "start": start,
                        "end": end,
                    },
                },
                {
                    "moduleName": "homework",
                    "endpointName": "get-homework",
                    "parameters": {"student": {"id": student["id"]}},
                },
                # The web app sums the per-subject rows to get the overall
                # absence figure, so only this one call is needed for both.
                {
                    "moduleName": "classbook",
                    "endpointName": "get-statistics",
                    "parameters": {
                        "student": {"id": student["id"]},
                        "from": term_start,
                        "until": today.isoformat(),
                        "type": CLASSBOOK_STATISTIC_TYPE,
                        "by": "subject",
                        "unexcusedOnly": False,
                        "includeInternalExemptions": False,
                    },
                },
                {
                    "moduleName": "classbook",
                    "endpointName": "get-statistics",
                    "parameters": {
                        "student": {"id": student["id"]},
                        "from": term_start,
                        "until": today.isoformat(),
                        "type": CLASSBOOK_STATISTIC_TYPE,
                        "by": "subject",
                        "unexcusedOnly": True,
                        "includeInternalExemptions": False,
                    },
                },
                {
                    "moduleName": "classbook",
                    "endpointName": "get-student-absence-statistic",
                    "parameters": {
                        "studentId": student["id"],
                        "start": term_start,
                        "end": today.isoformat(),
                    },
                },
                {
                    "moduleName": "classbook",
                    "endpointName": "get-entry-statistics",
                    "parameters": {
                        "student": {"id": student["id"]},
                        "termId": term_id,
                    },
                },
            )
        ]

        results = await self.async_call_batch_lenient(per_student)

        data: dict[str, Any] = {
            "class_hours": class_hours_raw or [],
            "letters": letters or [],
            "events": events or {},
            "event_categories": categories or [],
            "term": term,
            "calendar_window": [calendar_start, calendar_end],
            "students": {},
        }
        per_student_calls = 7
        for index, student in enumerate(students):
            offset = index * per_student_calls
            (
                lessons,
                exams,
                homework,
                absence_by_subject,
                unexcused_by_subject,
                absence_days,
                entries,
            ) = results[offset : offset + per_student_calls]
            data["students"][str(student["id"])] = {
                "info": student,
                "lessons": lessons or [],
                "exams": exams or [],
                "homework": homework or [],
                "absence_by_subject": absence_by_subject or [],
                "unexcused_by_subject": unexcused_by_subject or [],
                "absence_days": absence_days or {},
                "classbook_entries": entries,
            }
        return data


async def _safe_json(response: ClientResponse) -> Any:
    """Decode a JSON body, tolerating a non-JSON error page."""
    try:
        return await response.json(content_type=None)
    except (ValueError, ClientError):
        return None
