"""Metergy API client with cookie-based authentication.

This client uses Home Assistant's shared aiohttp session and supports both:
1. Manual cookie input (for quick setup)
2. Username/password login to automatically obtain cookies

The login flow follows Metergy's authentication:
- Step 1: GET /home to retrieve initial session and CSRF token
- Step 2: POST /authentication/login with credentials
- Step 3: Extract and merge cookies from response
"""

from __future__ import annotations

import asyncio
from datetime import date
import logging
import re
from typing import Any

from aiohttp import ClientError, ClientResponse, FormData
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession


BASE_URL = "https://www.mymetergysolutions.com/metergysolutions/consumption/"
AUTH_HOME_URL = "https://www.mymetergysolutions.com/home"
AUTH_LOGIN_URL = "https://www.mymetergysolutions.com/authentication/login"


class MetergyApiError(Exception):
    """Raised when the Metergy API call fails or returns unexpected data."""


class MetergyAuthError(Exception):
    """Raised when login/authentication fails."""


class MetergyClient:
    """HTTP client for Metergy consumption endpoints with login support."""

    def __init__(
        self,
        hass: HomeAssistant,
        meter_id: str,
        cookie: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the client with automatic login capability.

        Args:
            hass: Home Assistant instance
            meter_id: Metergy meter ID
            cookie: Initial cookie string (typically empty, will auto-login)
            username: Username (email) for auto-login
            password: Password for auto-login
        """
        self._hass = hass
        self._meter_id = meter_id
        self._cookie = cookie
        self._username = username
        self._password = password
        self._session = async_get_clientsession(hass)
        self._log = logging.getLogger(__name__)
        self._login_lock = asyncio.Lock()  # Prevent concurrent login attempts

    @staticmethod
    def _extract_cookies_from_response(resp: ClientResponse) -> dict[str, str]:
        """Extract cookies from response Set-Cookie headers into a dict."""
        cookies = {}
        for cookie in resp.cookies.values():
            cookies[cookie.key] = cookie.value
        return cookies

    @staticmethod
    def _format_cookie_string(cookies: dict[str, str]) -> str:
        """Format cookie dict into a Cookie header string."""
        return "; ".join(f"{key}={value}" for key, value in cookies.items())

    @staticmethod
    def _client_error_summary(err: ClientError) -> str:
        """Return ClientError details without embedding request URLs or headers."""
        status = getattr(err, "status", None)
        message = getattr(err, "message", None)
        if status is not None:
            if message:
                return f"{err.__class__.__name__}: HTTP {status} ({message})"
            return f"{err.__class__.__name__}: HTTP {status}"

        os_error = getattr(err, "os_error", None)
        if os_error:
            return f"{err.__class__.__name__}: {os_error}"

        return err.__class__.__name__

    async def login(self) -> str:
        """Perform login to obtain authentication cookies.

        Uses a lock to prevent concurrent login attempts. If another request
        is already performing login, subsequent requests will wait and reuse
        the refreshed cookie instead of logging in again.

        Returns:
            Cookie string to use for API requests

        Raises:
            MetergyAuthError: If login fails
        """

        # Remember the cookie value before acquiring lock
        old_cookie = self._cookie

        async with self._login_lock:
            # Check if cookie was refreshed by another concurrent request
            # while we were waiting for the lock (simple string comparison)
            if self._cookie != old_cookie and self._cookie and self._cookie.strip():
                self._log.debug(
                    "Cookie already refreshed by concurrent request, skipping login"
                )
                return self._cookie

            self._log.debug("Starting Metergy login flow")

            try:
                # Step 1: GET /home to obtain initial session and CSRF token
                async with self._session.get(AUTH_HOME_URL) as resp:
                    if resp.status != 200:
                        raise MetergyAuthError(
                            f"Failed to load home page: HTTP {resp.status}"
                        )
                    initial_cookies = self._extract_cookies_from_response(resp)
                    html = await resp.text()

                    # Extract CSRF token from HTML form (not from cookie!)
                    # The cookie token and form token are different values
                    token_match = re.search(
                        r'<input[^>]*name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']',
                        html,
                    )
                    if not token_match:
                        # Try alternative pattern (value before name)
                        token_match = re.search(
                            r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']__RequestVerificationToken["\']',
                            html,
                        )

                    if not token_match:
                        raise MetergyAuthError("CSRF token not found in HTML form")

                    csrf_token = token_match.group(1)
                    self._log.debug(
                        "Retrieved initial session with %d cookies and form token",
                        len(initial_cookies),
                    )

                # Step 2: POST login with credentials and CSRF token
                form = FormData()
                form.add_field("username", self._username)
                form.add_field("password", self._password)
                form.add_field("__RequestVerificationToken", csrf_token)
                form.add_field("g-recaptcha-response", "disabled")

                # Build cookie header from initial cookies
                cookie_header = self._format_cookie_string(initial_cookies)
                headers = {
                    "Cookie": cookie_header,
                    "Accept": "application/json",
                    "Origin": "https://www.mymetergysolutions.com",
                    "Referer": "https://www.mymetergysolutions.com/home",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }

                async with self._session.post(
                    AUTH_LOGIN_URL, data=form, headers=headers
                ) as resp:
                    if resp.status != 200:
                        raise MetergyAuthError(f"Login failed: HTTP {resp.status}")

                    # Step 3: Extract and merge cookies
                    login_cookies = self._extract_cookies_from_response(resp)

                    # Check for authentication cookies (iportal-v1 is the key auth cookie)
                    if "iportal-v1" not in login_cookies:
                        raise MetergyAuthError(
                            "Login response missing authentication cookie (iportal-v1)"
                        )

                    # Merge initial and login cookies (login cookies override)
                    all_cookies = {**initial_cookies, **login_cookies}
                    final_cookie_string = self._format_cookie_string(all_cookies)

                    self._log.info(
                        "Metergy login successful, obtained %d cookies",
                        len(all_cookies),
                    )

                    # Update internal cookie
                    self._cookie = final_cookie_string
                    return final_cookie_string

            except ClientError as err:
                raise MetergyAuthError(
                    f"HTTP error during login: {self._client_error_summary(err)}"
                ) from err
            except MetergyAuthError:
                raise
            except Exception as err:  # noqa: BLE001
                raise MetergyAuthError(
                    f"Unexpected error during login: {err.__class__.__name__}"
                ) from err

    async def test_authentication(self) -> bool:
        """Test if current cookie is valid by sending a lightweight API request.

        Returns:
            True if cookie is valid, False otherwise
        """
        if not self._cookie or not self._cookie.strip():
            return False

        # Test with a lightweight request (fetch just 1 day of electricity data)
        from datetime import date, timedelta

        test_date = date.today() - timedelta(days=2)

        url = URL(BASE_URL + str(self._meter_id)).with_query(
            {
                "serviceType": "Electricity",
                "interval": "Daily",
                "fromDate": test_date.strftime("%m/%d/%Y"),
                "toDate": test_date.strftime("%m/%d/%Y"),
                "compare": "false",
            }
        )

        headers = {
            "Accept": "application/json",
            "Cookie": self._cookie,
            "Referer": "https://www.mymetergysolutions.com/metergysolutions/usage",
        }

        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status in (401, 403):
                    self._log.debug(
                        "Cookie test failed: HTTP %d (auth required)", resp.status
                    )
                    return False
                if resp.status == 200:
                    self._log.debug("Cookie test passed: authentication valid")
                    return True
                # Other errors don't necessarily mean auth failure
                self._log.warning(
                    "Cookie test inconclusive: HTTP %d (assuming valid)", resp.status
                )
                return True
        except Exception as err:  # noqa: BLE001
            self._log.warning(
                "Cookie test error: %s (assuming valid)", err.__class__.__name__
            )
            return True  # Don't re-login on network errors

    async def test_and_refresh_auth(self) -> None:
        """Test current authentication and refresh if needed.

        This method should be called before batch operations (backfill, scheduled imports)
        to ensure cookie is valid and avoid repeated login attempts during concurrent requests.
        """
        is_valid = await self.test_authentication()
        if not is_valid:
            self._log.info("Cookie invalid or expired, performing login")
            await self.login()
        else:
            self._log.debug("Cookie is valid, no re-login needed")

    async def fetch_consumption(
        self,
        service_type: str,  # "Electricity" | "ColdWater" | "HotWater"
        interval: str,  # "Hourly" | "Daily" | "Monthly"
        from_day: date,
        to_day: date,
    ) -> Any:
        """Fetch raw JSON from Metergy for the given parameters.

        Automatically retries with fresh login on 401/403 errors as fallback.
        For batch operations, call test_and_refresh_auth() once before the batch
        to avoid repeated login attempts.

        Returns:
            Parsed JSON response (Python objects)

        Raises:
            MetergyApiError: On HTTP errors or unexpected failures
        """

        url = URL(BASE_URL + str(self._meter_id)).with_query(
            {
                "serviceType": service_type,
                "interval": interval,
                "fromDate": from_day.strftime("%m/%d/%Y"),
                "toDate": to_day.strftime("%m/%d/%Y"),
                "compare": "false",
            }
        )

        headers = {
            "Accept": "application/json",
            "Cookie": self._cookie,
            "Referer": "https://www.mymetergysolutions.com/metergysolutions/usage",
        }

        try:
            self._log.debug(
                "Metergy request: service=%s interval=%s from=%s to=%s",
                service_type,
                interval,
                from_day,
                to_day,
            )
            async with self._session.get(url, headers=headers) as resp:
                # Handle authentication errors with retry (fallback mechanism)
                # Note: For batch operations, call test_and_refresh_auth() beforehand
                # to avoid this. This retry serves as a safety net for edge cases.
                if resp.status in (401, 403):
                    self._log.warning(
                        "Authentication failed (HTTP %d), attempting re-login",
                        resp.status,
                    )
                    await self.login()  # Protected by lock, safe even if concurrent
                    # Retry with new cookie
                    headers["Cookie"] = self._cookie
                    async with self._session.get(url, headers=headers) as retry_resp:
                        if retry_resp.status in (401, 403):
                            raise MetergyApiError(
                                f"Authentication failed even after re-login: "
                                f"HTTP {retry_resp.status}"
                            )
                        retry_resp.raise_for_status()
                        data = await retry_resp.json(content_type=None)
                        self._log.info("Request succeeded after re-login")
                        return data

                resp.raise_for_status()
                data = await resp.json(content_type=None)
                self._log.debug(
                    "Metergy response OK: status=%s len=%s",
                    resp.status,
                    (len(data) if isinstance(data, list) else "n/a"),
                )
                return data
        except ClientError as err:
            raise MetergyApiError(
                f"HTTP error calling Metergy: {self._client_error_summary(err)}"
            ) from err
        except MetergyApiError:
            raise
        except Exception as err:  # noqa: BLE001
            raise MetergyApiError(
                f"Unexpected error calling Metergy: {err.__class__.__name__}"
            ) from err

    @staticmethod
    def extract_hourly_kwh_list(payload: Any) -> list[float]:
        """Extract the 24 hourly kWh values from the sample payload.

        The expected shape is: payload[0].dataSetLists[0].dataSets[0].data[].value
        """
        try:
            root = payload[0]
            ds = root["dataSetLists"][0]["dataSets"][0]["data"]
            return [float(item["value"]) for item in ds]
        except Exception as err:  # noqa: BLE001
            raise MetergyApiError(f"Invalid hourly payload format: {err}") from err

    @staticmethod
    def extract_daily_volume_list(payload: Any) -> list[float]:
        """Extract the per-day volume list for water, using Daily interval.

        Expected to be in the same FusionCharts-like structure as hourly but with
        one value per day. The caller maps each value to a midnight timestamp.
        """
        try:
            root = payload[0]
            ds = root["dataSetLists"][0]["dataSets"][0]["data"]
            return [float(item["value"]) for item in ds]
        except Exception as err:  # noqa: BLE001
            raise MetergyApiError(f"Invalid daily payload format: {err}") from err

    @staticmethod
    def extract_unit_of_measure(payload: Any) -> str:
        """Return the unitOfMeasure string from payload (e.g. "kWh", "LTR")."""
        try:
            root = payload[0]
            return str(root.get("unitOfMeasure", ""))
        except Exception as err:  # noqa: BLE001
            raise MetergyApiError(
                f"Invalid payload when reading unitOfMeasure: {err}"
            ) from err
