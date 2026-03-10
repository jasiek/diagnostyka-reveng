#!/usr/bin/env python3
"""
Diagnostyka API client.

Authentication flow:
  1. Firebase email magic link sign-in (one-time)
  2. Firebase refresh token stored in ~/.diagnostyka_tokens.json
  3. Firebase idToken refreshed automatically (~1hr expiry)
  4. Backend API called with Authorization: Bearer <idToken>

Usage:
  # First-time login
  python diagnostyka.py login you@example.com

  # Check email, get the link, then:
  python diagnostyka.py verify <oobCode-from-email-link>

  # Now use the API
  python diagnostyka.py user
  python diagnostyka.py patient
  python diagnostyka.py results
  python diagnostyka.py products
  python diagnostyka.py institutions
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
    """Handles Firebase Auth email link flow and token management."""

    def __init__(self):
        self.tokens = self._load_tokens()

    def _load_tokens(self) -> dict:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text())
        return {}

    def _save_tokens(self, tokens: dict):
        self.tokens.update(tokens)
        TOKEN_FILE.write_text(json.dumps(self.tokens, indent=2))
        TOKEN_FILE.chmod(0o600)

    def send_sign_in_link(self, email: str):
        """Step 1: Request sign-in link via the Diagnostyka backend.

        The backend calls Firebase Admin SDK to send the email and
        creates a pending user record at the same time.  This is what
        the real mobile app does — going through the backend rather
        than calling Firebase client APIs directly.
        """
        resp = requests.post(
            f"{BACKEND_PROD}/api/v1/user/sign-in",
            headers={
                "User-Agent": DART_USER_AGENT,
                "Content-Type": "application/json; charset=utf-8",
                "accept-language": "pl",
            },
            json={
                "email": email,
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
        """Step 2: Complete sign-in with the code from the email link."""
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
        """Re-trigger the backend sign-in email (same as 'login' step).

        Normally not needed — use 'login' + 'verify' instead.
        """
        email = self.auth.tokens.get("email")
        if not email:
            raise RuntimeError("No email stored. Run 'login' first.")
        self.auth.send_sign_in_link(email)
        return {"status": "sign-in email sent", "email": email}

    def register_push_token(self, fcm_token: str) -> dict:
        """Register an FCM push notification token with the backend."""
        return self.post("/api/v1/push-notification/register-token", data={
            "token": fcm_token,
            "deviceId": self.auth.tokens.get("deviceId", str(uuid.uuid4())),
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
    sub.add_parser("signin", help="Sign in to Diagnostyka backend")
    sub.add_parser("token", help="Print current idToken")

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

    # Auth commands (don't need backend client)
    if args.command == "login":
        auth = DiagnostykaAuth()
        result = auth.send_sign_in_link(args.email)
        print(f"Magic link sent to {args.email}")
        print("Check your email and run:")
        print(f"  python diagnostyka.py verify <oobCode-or-full-URL-from-email>")
        return

    if args.command == "verify":
        auth = DiagnostykaAuth()
        code = extract_oob_code(args.code)
        result = auth.complete_sign_in(code)
        print(f"Signed in as {result.get('email', '?')}")
        print(f"Firebase UID: {result.get('localId', '?')}")
        print(f"Token stored in {TOKEN_FILE}")
        print()
        print("Ready! Try:  python diagnostyka.py user")
        return

    if args.command == "refresh":
        auth = DiagnostykaAuth()
        token = auth.refresh_id_token()
        print(f"Token refreshed. Expires in ~1 hour.")
        return

    if args.command == "token":
        auth = DiagnostykaAuth()
        print(auth.get_id_token())
        return

    # API commands
    client = DiagnostykaClient(base_url, language=args.language)

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
