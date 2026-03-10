#!/usr/bin/env python3
"""
Diagnostyka API client.

Authentication flow (mirrors the real mobile app):
  1. login  — POST /api/v1/user/sign-in with email + deviceToken + actionCodeSettings
              Backend creates/updates user record AND sends magic-link email.
  2. verify — Extract oobCode from the email link, call Firebase signInWithEmailLink
              to get idToken/refreshToken.  Device is now verified on the backend.
  3. Any API call — Authorization: Bearer <idToken>, auto-refreshed.

Usage:
  python diagnostyka.py login you@example.com
  # check email, copy the link
  python diagnostyka.py verify '<full-URL-or-oobCode>'
  python diagnostyka.py user
  python diagnostyka.py results
  python diagnostyka.py get /api/v1/any/endpoint
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

FIREBASE_API_KEY = "AIzaSyBIIQT2arhhBulESRS7ZPl3vQQ8zwU0w_Y"
FIREBASE_PROJECT_ID = "apps-for-dia"
CONTINUE_URL = "https://apps-for-dia.web.app"

BACKEND_PROD = "https://mobile-fir-backend.diag.pl"
BACKEND_STAGING = "https://mobile-fir-backend-staging.diag.pl"

TOKEN_FILE = Path.home() / ".diagnostyka_tokens.json"

# App identity — must match the real APK to be indistinguishable
APP_VERSION = "2.0.7"
APP_BUILD_NUMBER = "996"
APP_PACKAGE = "pl.diagnostyka.mobile"

# Dart 3.10.1 is the runtime in this Flutter build; dart:io HttpClient
# sets this User-Agent by default and Dio inherits it.
DART_USER_AGENT = "Dart/3.10 (dart:io)"


class DiagnostykaAuth:
    """Handles Firebase Auth email link flow and token management.

    Persists all state (tokens, email, deviceToken) to TOKEN_FILE.
    """

    def __init__(self):
        self.tokens = self._load_tokens()
        # Ensure a stable deviceToken exists (like FlutterSecureStorage on the real app)
        if "deviceToken" not in self.tokens:
            self._save_tokens({"deviceToken": str(uuid.uuid4())})

    def _load_tokens(self) -> dict:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text())
        return {}

    def _save_tokens(self, tokens: dict):
        self.tokens.update(tokens)
        TOKEN_FILE.write_text(json.dumps(self.tokens, indent=2))
        TOKEN_FILE.chmod(0o600)

    @property
    def device_token(self) -> str:
        return self.tokens["deviceToken"]

    def send_sign_in_link(self, email: str, base_url: str = BACKEND_PROD):
        """Step 1: Request sign-in email via the Diagnostyka backend.

        The backend calls Firebase Admin SDK to send the email AND
        creates/updates the user + device record.  The deviceToken ties
        this sign-in attempt to our "device".
        """
        resp = requests.post(
            f"{base_url}/api/v1/user/sign-in",
            headers={
                "User-Agent": DART_USER_AGENT,
                "Content-Type": "application/json; charset=utf-8",
                "accept-language": "pl",
            },
            json={
                "email": email,
                "deviceToken": self.device_token,
                "actionCodeSettings": {
                    "url": CONTINUE_URL,
                    "handleCodeInApp": True,
                    "android": {
                        "packageName": APP_PACKAGE,
                        "installApp": False,
                        "minimumVersion": "1",
                    },
                },
            },
        )
        resp.raise_for_status()
        self._save_tokens({"email": email})
        return resp.json() if resp.text else {}

    def complete_sign_in(self, oob_code: str):
        """Step 2: Complete sign-in with the oobCode from the email link.

        Calls Firebase signInWithEmailLink to exchange the code for
        idToken + refreshToken.  After this, the backend considers the
        device (identified by deviceToken) as verified.
        """
        email = self.tokens.get("email")
        if not email:
            raise RuntimeError("No email stored. Run 'login' first.")

        resp = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithEmailLink?key={FIREBASE_API_KEY}",
            json={
                "email": email,
                "oobCode": oob_code,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._save_tokens({
            "idToken": data["idToken"],
            "refreshToken": data["refreshToken"],
            "localId": data["localId"],
            "expiresAt": time.time() + int(data.get("expiresIn", 3600)),
        })
        return data

    def refresh_id_token(self):
        """Refresh the Firebase idToken using the stored refresh token."""
        refresh_token = self.tokens.get("refreshToken")
        if not refresh_token:
            raise RuntimeError("No refresh token. Run 'login' first.")

        resp = requests.post(
            f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._save_tokens({
            "idToken": data["id_token"],
            "refreshToken": data["refresh_token"],
            "expiresAt": time.time() + int(data.get("expires_in", 3600)),
        })
        return data["id_token"]

    def get_id_token(self) -> str:
        """Get a valid idToken, refreshing if needed."""
        expires_at = self.tokens.get("expiresAt", 0)
        if time.time() > expires_at - 60:  # refresh 60s before expiry
            return self.refresh_id_token()
        token = self.tokens.get("idToken")
        if not token:
            raise RuntimeError("No token. Run 'login' first.")
        return token


class DiagnostykaClient:
    """Client for the Diagnostyka backend API.

    Mimics the real Flutter app's HTTP fingerprint:
    - User-Agent matches Dart's dart:io HttpClient default
    - accept-language set via Dio interceptor (languageHeader)
    - Content-Type only on requests with a body (Dio behaviour)
    - Connection keep-alive (requests.Session handles this)
    """

    def __init__(self, base_url: str = BACKEND_PROD, language: str = "pl"):
        self.base_url = base_url.rstrip("/")
        self.auth = DiagnostykaAuth()
        self.language = language
        self.session = requests.Session()
        # Session-level headers that every request carries, matching the app
        self.session.headers.update({
            "User-Agent": DART_USER_AGENT,
            "accept-language": self.language,
            "Accept": "application/json",
        })

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth.get_id_token()}",
        }

    def get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {**self._auth_headers(), "Content-Type": "application/json; charset=utf-8"}
        resp = self.session.post(url, headers=headers, json=data)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {**self._auth_headers(), "Content-Type": "application/json; charset=utf-8"}
        resp = self.session.put(url, headers=headers, json=data)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.delete(url, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    # ===== Convenience methods =====

    def sign_in(self) -> dict:
        """Re-trigger the backend sign-in email (same as 'login' step)."""
        email = self.auth.tokens.get("email")
        if not email:
            raise RuntimeError("No email stored. Run 'login' first.")
        self.auth.send_sign_in_link(email, base_url=self.base_url)
        return {"status": "sign-in email sent", "email": email}

    def register_push_token(self, fcm_token: str) -> dict:
        """Register an FCM push notification token with the backend."""
        return self.post("/api/v1/push-notification/register-token", data={
            "token": fcm_token,
            "deviceId": self.auth.device_token,
            "operatingSystem": "Android",
            "operatingSystemVersion": "14",
            "appVersion": APP_VERSION,
            "buildNumber": APP_BUILD_NUMBER,
        })

    def user(self) -> dict:
        return self.get("/api/v1/user")

    def patient_data(self) -> dict:
        return self.get("/api/v1/patient-data")

    def health_profile(self) -> dict:
        return self.get("/api/v1/my-health-profile")

    def device_verified(self) -> dict:
        return self.get("/api/device/verified")

    def consents(self) -> dict:
        return self.get("/api/v1/consent")

    def results(self) -> dict:
        return self.get("/api/v1/csw")

    def results_orders(self) -> dict:
        return self.get("/api/v1/csw/patient/order")

    def results_document(self, doc_id: str) -> dict:
        return self.get(f"/api/v1/csw/document/{doc_id}")

    def results_history(self, param_id: str) -> dict:
        return self.get(f"/api/v1/csw/patient/history/{param_id}")

    def order_history(self) -> dict:
        return self.get("/api/v1/order-history")

    def voucher(self, voucher_id: str) -> dict:
        return self.get(f"/api/v1/order-history/voucher/{voucher_id}")

    def products(self, page: int = None, page_size: int = None) -> dict:
        params = {}
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["pageSize"] = page_size
        return self.get("/api/v1/eshop/products", params=params)

    def product(self, product_id: str) -> dict:
        return self.get(f"/api/v1/eshop/products/{product_id}")

    def categories(self) -> dict:
        return self.get("/api/v1/eshop/products/categories")

    def search_tests(self, query: str) -> dict:
        return self.get("/api/v1/eshop/search/blood-tests", params={"query": query})

    def search_packages(self, query: str) -> dict:
        return self.get("/api/v1/eshop/search/packages", params={"query": query})

    def popular_searches(self) -> dict:
        return self.get("/api/v1/eshop/search/popular-search")

    def institutions(self, city: str = None, lat: float = None, lng: float = None) -> dict:
        params = {}
        if city:
            params["city"] = city
        if lat is not None:
            params["latitude"] = lat
        if lng is not None:
            params["longitude"] = lng
        return self.get("/api/v1/institution", params=params)

    def institution(self, inst_id: str) -> dict:
        return self.get(f"/api/v1/institution/{inst_id}")

    def institution_cities(self) -> dict:
        return self.get("/api/v1/institution/city")

    def current_institution(self) -> dict:
        return self.get("/api/v1/institution/current")

    def profilaktometr(self) -> dict:
        return self.get("/api/v1/profilaktometr")

    def profilaktometr_config(self) -> dict:
        return self.get("/api/v1/profilaktometr/config")

    def medical_parameters(self) -> dict:
        return self.get("/api/v1/profilaktometr/medical-parameters")

    def assistant_topics(self) -> dict:
        return self.get("/api/v1/assistant/topics")

    def assistant_session(self) -> dict:
        return self.post("/api/v1/assistant/session")

    def assistant_message(self, session_id: str, message: str) -> dict:
        return self.post("/api/v1/assistant/conversation", data={
            "sessionId": session_id,
            "message": message,
        })

    def cart(self) -> dict:
        return self.get("/api/v2/cart")

    def gus_search(self, nip: str = None, regon: str = None) -> dict:
        params = {}
        if nip:
            params["nip"] = nip
        if regon:
            params["regon"] = regon
        return self.get("/api/v1/gus/search", params=params)

    # ===== Identity verification (mObywatel) =====

    def start_mobywatel_verification(self) -> dict:
        """Initiate identity verification via mObywatel.

        The backend creates a verification session with the government
        Back System and returns a 6-digit code (+ possibly QR data and
        expiry info). The user must enter this code in their mObywatel app.
        """
        return self.post("/api/v1/m-obywatel")

    def mobywatel_identify(self, poll_interval: int = 5, timeout: int = 300):
        """Full mObywatel identity verification flow.

        1. POST /api/v1/m-obywatel to get verification code
        2. Display the code (numeric + QR) for the user
        3. Poll GET /api/device/verified until isVerified=true or timeout
        """
        print("Starting mObywatel identity verification...")
        print()

        # Step 1: initiate
        try:
            resp = self.start_mobywatel_verification()
        except requests.HTTPError as e:
            print(f"Failed to start verification: HTTP {e.response.status_code}", file=sys.stderr)
            print(e.response.text, file=sys.stderr)
            return None

        # Show the raw response so we can see the full shape
        pp(resp)
        print()

        # Try to extract and display a verification code
        # We don't know the exact response shape yet, so try common field names
        code = None
        for key in ("code", "verificationCode", "shareCode", "otp"):
            if key in resp:
                code = str(resp[key])
                break
        # If response is just a string/number, use it directly
        if code is None and isinstance(resp, (str, int)):
            code = str(resp)

        if code:
            print(f"  Verification code:  {code}")
            print()
            # Render QR code in terminal for scanning
            try:
                import qrcode
                qr = qrcode.QRCode(border=1, box_size=1)
                qr.add_data(code)
                qr.make(fit=True)
                qr.print_ascii(invert=True)
                print()
            except ImportError:
                pass

        print("=" * 50)
        print("  Open the mObywatel app on your phone:")
        print('  1. Tap "Kod QR"')
        print('  2. Select "Potwierdz swoje dane"')
        if code:
            print(f"  3. Scan the QR above or type: {code}")
        else:
            print("  3. Enter the code shown above")
        print("  4. Approve sharing your data")
        print("=" * 50)
        print()

        # Step 2: poll for completion
        print(f"Polling for verification (every {poll_interval}s, timeout {timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(poll_interval)
            elapsed = int(time.time() - start)
            try:
                status = self.device_verified()
            except requests.HTTPError:
                print(f"  [{elapsed}s] Error checking status, retrying...")
                continue

            is_verified = status.get("isVerified", False)
            is_new = status.get("isNewDevice", False)
            print(f"  [{elapsed}s] isVerified={is_verified}, isNewDevice={is_new}")

            if is_verified:
                print()
                print("Identity verified!")
                return status

        print()
        print("Timed out waiting for verification.", file=sys.stderr)
        return None


def extract_oob_code(url_or_code: str) -> str:
    """Extract oobCode from a Firebase email link URL, or return as-is if already a code.

    Firebase email sign-in links can come in several forms:
    1. Direct: https://.../__/auth/action?mode=signIn&oobCode=ABC
    2. Dynamic Link wrapper: https://diaglogin.page.link/XXXX?link=https%3A...%26oobCode%3DABC
    3. Short Dynamic Link that must be followed (redirect)
    """
    if not url_or_code.startswith("http"):
        return url_or_code

    parsed = urlparse(url_or_code)
    params = parse_qs(parsed.query)

    # Direct oobCode in top-level query
    if "oobCode" in params:
        return params["oobCode"][0]

    # Dynamic Link wrapper — oobCode is inside the nested 'link' param
    if "link" in params:
        return extract_oob_code(params["link"][0])

    # Try fragment (some Firebase configs put params there)
    frag_params = parse_qs(parsed.fragment)
    if "oobCode" in frag_params:
        return frag_params["oobCode"][0]

    # Short Dynamic Link (e.g. https://diaglogin.page.link/XXXX) — follow redirect
    try:
        resp = requests.get(url_or_code, allow_redirects=False)
        location = resp.headers.get("Location", "")
        if location and location != url_or_code:
            return extract_oob_code(location)
    except requests.RequestException:
        pass

    raise ValueError(f"Could not find oobCode in URL: {url_or_code}")


def pp(data):
    """Pretty-print JSON response."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Diagnostyka API client")
    parser.add_argument("--staging", action="store_true", help="Use staging backend")
    parser.add_argument("--language", default="pl", help="Language header (default: pl)")
    sub = parser.add_subparsers(dest="command")

    # Auth commands
    login_p = sub.add_parser("login", help="Send magic link to email")
    login_p.add_argument("email", help="Your email address")

    verify_p = sub.add_parser("verify", help="Complete sign-in with link/code from email")
    verify_p.add_argument("code", help="oobCode from email link (URL or code)")

    sub.add_parser("refresh", help="Refresh the Firebase token")
    sub.add_parser("signin", help="Re-send sign-in email")
    sub.add_parser("token", help="Print current idToken")
    sub.add_parser("status", help="Show auth status and deviceToken")

    # API commands
    sub.add_parser("user", help="Get user profile")
    sub.add_parser("patient", help="Get patient data")
    sub.add_parser("health", help="Get health profile")
    sub.add_parser("verified", help="Check device verification status")
    sub.add_parser("consents", help="Get consent statuses")
    sub.add_parser("results", help="Get lab results (CSW)")
    sub.add_parser("orders", help="Get result orders")
    sub.add_parser("order-history", help="Get order history")
    sub.add_parser("products", help="List e-shop products")
    sub.add_parser("categories", help="List product categories")
    sub.add_parser("popular", help="Get popular searches")
    sub.add_parser("institutions", help="List institutions")
    sub.add_parser("cities", help="List cities with institutions")
    sub.add_parser("profilaktometr", help="Get profilaktometr data")
    sub.add_parser("assistant-topics", help="Get AI assistant topics")

    id_p = sub.add_parser("identify", help="Verify identity via mObywatel")
    id_p.add_argument("--poll", type=int, default=5, help="Poll interval in seconds (default: 5)")
    id_p.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")

    search_p = sub.add_parser("search", help="Search blood tests")
    search_p.add_argument("query", help="Search query")

    inst_p = sub.add_parser("institution", help="Get institution details")
    inst_p.add_argument("id", help="Institution ID")

    product_p = sub.add_parser("product", help="Get product details")
    product_p.add_argument("id", help="Product ID")

    doc_p = sub.add_parser("document", help="Get result document")
    doc_p.add_argument("id", help="Document ID")

    history_p = sub.add_parser("history", help="Get parameter history")
    history_p.add_argument("id", help="Parameter ID")

    voucher_p = sub.add_parser("voucher", help="Get voucher details")
    voucher_p.add_argument("id", help="Voucher ID")

    gus_p = sub.add_parser("gus", help="Search GUS registry")
    gus_p.add_argument("--nip", help="NIP number")
    gus_p.add_argument("--regon", help="REGON number")

    get_p = sub.add_parser("get", help="GET any endpoint")
    get_p.add_argument("path", help="API path (e.g. /api/v1/user)")

    post_p = sub.add_parser("post", help="POST to any endpoint")
    post_p.add_argument("path", help="API path")
    post_p.add_argument("--data", help="JSON body", default="{}")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    base_url = BACKEND_STAGING if args.staging else BACKEND_PROD

    # Auth commands (don't need full backend client)
    if args.command == "login":
        auth = DiagnostykaAuth()
        print(f"Device token: {auth.device_token}")
        auth.send_sign_in_link(args.email, base_url=base_url)
        print(f"Magic link sent to {args.email}")
        print("Check your email and run:")
        print(f"  python diagnostyka.py verify '<full-URL-or-oobCode>'")
        return

    if args.command == "verify":
        auth = DiagnostykaAuth()
        code = extract_oob_code(args.code)
        result = auth.complete_sign_in(code)
        print(f"Signed in as {result.get('email', '?')}")
        print(f"Firebase UID: {result.get('localId', '?')}")
        print(f"Device token: {auth.device_token}")
        print(f"Token stored in {TOKEN_FILE}")
        print()
        print("Ready! Try:  python diagnostyka.py user")
        return

    if args.command == "refresh":
        auth = DiagnostykaAuth()
        auth.refresh_id_token()
        print("Token refreshed. Expires in ~1 hour.")
        return

    if args.command == "token":
        auth = DiagnostykaAuth()
        print(auth.get_id_token())
        return

    if args.command == "status":
        auth = DiagnostykaAuth()
        print(f"Token file:   {TOKEN_FILE}")
        print(f"Email:        {auth.tokens.get('email', '(none)')}")
        print(f"Device token: {auth.device_token}")
        print(f"Firebase UID: {auth.tokens.get('localId', '(none)')}")
        has_token = bool(auth.tokens.get("idToken"))
        expires_at = auth.tokens.get("expiresAt", 0)
        expired = time.time() > expires_at
        print(f"Has idToken:  {has_token}  {'(expired)' if has_token and expired else ''}")
        return

    # API commands
    client = DiagnostykaClient(base_url, language=args.language)

    # Interactive commands (not simple JSON responses)
    if args.command == "identify":
        try:
            result = client.mobywatel_identify(
                poll_interval=args.poll, timeout=args.timeout,
            )
            if result is None:
                sys.exit(1)
        except requests.HTTPError as e:
            print(f"HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
            sys.exit(1)
        return

    try:
        handlers = {
            "signin": lambda: client.sign_in(),
            "user": lambda: client.user(),
            "patient": lambda: client.patient_data(),
            "health": lambda: client.health_profile(),
            "verified": lambda: client.device_verified(),
            "consents": lambda: client.consents(),
            "results": lambda: client.results(),
            "orders": lambda: client.results_orders(),
            "order-history": lambda: client.order_history(),
            "products": lambda: client.products(),
            "categories": lambda: client.categories(),
            "popular": lambda: client.popular_searches(),
            "institutions": lambda: client.institutions(),
            "cities": lambda: client.institution_cities(),
            "profilaktometr": lambda: client.profilaktometr(),
            "assistant-topics": lambda: client.assistant_topics(),
            "search": lambda: client.search_tests(args.query),
            "institution": lambda: client.institution(args.id),
            "product": lambda: client.product(args.id),
            "document": lambda: client.results_document(args.id),
            "history": lambda: client.results_history(args.id),
            "voucher": lambda: client.voucher(args.id),
            "gus": lambda: client.gus_search(nip=args.nip, regon=args.regon),
            "get": lambda: client.get(args.path),
            "post": lambda: client.post(args.path, json.loads(args.data)),
        }
        result = handlers[args.command]()
        pp(result)
    except requests.HTTPError as e:
        print(f"HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
