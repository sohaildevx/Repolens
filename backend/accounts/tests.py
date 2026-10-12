"""Tests for the accounts authentication endpoints.

Covers the 12 required scenarios from the Phase 2 spec, plus:
- CSRF enforcement is actually active (not disabled)
- Email normalization (whitespace + lowercase)

DRF's APIClient stores session cookies between requests, exactly like a
browser would, so login -> me -> logout flows are tested realistically.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

User = get_user_model()

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"
ME_URL = "/api/auth/me/"
CSRF_URL = "/api/auth/csrf/"

VALID_PASSWORD = "v3ry-Secure-Pass!"


def register_payload(**overrides):
    payload = {
        "username": "alice_dev",
        "email": "alice@example.com",
        "password": VALID_PASSWORD,
        "password_confirm": VALID_PASSWORD,
    }
    payload.update(overrides)
    return payload


class RegisterTests(APITestCase):
    """Spec items 1-5, 12: registration flows."""

    def setUp(self):
        self.client = APIClient()

    def test_successful_registration(self):
        """(1) Valid payload -> 201, user persisted, no password echoed back."""
        response = self.client.post(REGISTER_URL, register_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username="alice_dev").exists())
        self.assertIn("user", response.data)
        self.assertNotIn("password", response.data["user"])

        # Response must contain safe fields only.
        user_data = response.data["user"]
        self.assertEqual(set(user_data.keys()),
                         {"id", "username", "email", "date_joined", "last_login"})

    def test_registration_missing_required_fields(self):
        """(2) Empty body -> 400 with per-field errors."""
        response = self.client.post(REGISTER_URL, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        for field in ("username", "email", "password", "password_confirm"):
            self.assertIn(field, response.data)

    def test_registration_missing_password_confirmation(self):
        """(2b) Omitting password_confirm -> 400."""
        payload = register_payload()
        payload.pop("password_confirm")

        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirm", response.data)

    def test_registration_duplicate_username(self):
        """(3) Existing username -> 400, no second user created."""
        User.objects.create_user(
            username="alice_dev", email="other@example.com", password=VALID_PASSWORD
        )

        response = self.client.post(
            REGISTER_URL, register_payload(email="fresh@example.com"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)
        self.assertEqual(User.objects.filter(username="alice_dev").count(), 1)

    def test_registration_duplicate_email(self):
        """(4) Existing email -> 400, rejected even with a different username."""
        User.objects.create_user(
            username="someone_else", email="alice@example.com", password=VALID_PASSWORD
        )

        response = self.client.post(
            REGISTER_URL, register_payload(username="brand_new"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.assertFalse(User.objects.filter(username="brand_new").exists())

    def test_registration_weak_password_rejected(self):
        """(5) Password failing Django validators -> 400 with password errors."""
        response = self.client.post(
            REGISTER_URL,
            register_payload(password="12345678", password_confirm="12345678"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="alice_dev").exists())

    def test_password_mismatch_rejected(self):
        """Password + confirmation differ -> 400."""
        response = self.client.post(
            REGISTER_URL,
            register_payload(password_confirm="Different-Pass!9"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirm", response.data)

    def test_password_stored_hashed_not_plaintext(self):
        """(12) DB stores a one-way hash; plain text never persisted."""
        self.client.post(REGISTER_URL, register_payload(), format="json")

        user = User.objects.get(username="alice_dev")
        self.assertNotEqual(user.password, VALID_PASSWORD)
        self.assertFalse(VALID_PASSWORD in user.password)
        self.assertTrue(user.has_usable_password())
        # Django's default hasher is PBKDF2 (pbkdf2_sha256_...)
        self.assertTrue(
            user.password.startswith(("pbkdf2_", "argon2", "bcrypt"))
        )
        # The hash still verifies the original password.
        self.assertTrue(user.check_password(VALID_PASSWORD))

    def test_email_normalized_to_lowercase(self):
        """(bonus) Whitespace stripped, domain case preserved semantics -> lowercased."""
        self.client.post(
            REGISTER_URL,
            register_payload(username="bob_dev", email="  Bob.Dev@Example.COM "),
            format="json",
        )

        user = User.objects.get(username="bob_dev")
        self.assertEqual(user.email, "bob.dev@example.com")


class LoginTests(APITestCase):
    """Spec items 6-7: login establishes a session; failures are generic."""

    def setUp(self):
        self.client = APIClient()
        User.objects.create_user(
            username="alice_dev", email="alice@example.com", password=VALID_PASSWORD
        )

    def test_successful_login(self):
        """(6) Correct credentials -> 200 + sessionid cookie issued."""
        response = self.client.post(
            LOGIN_URL,
            {"username": "alice_dev", "password": VALID_PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Session cookie exists and is bound to this user.
        self.assertIn("sessionid", response.cookies)
        user = User.objects.get(username="alice_dev")
        self.assertEqual(self.client.session.get("_auth_user_id"), str(user.pk))
        self.assertNotIn("password", response.data.get("user", {}))

    def test_login_invalid_credentials(self):
        """(7) Wrong password -> 401 with generic message (no user enumeration)."""
        response = self.client.post(
            LOGIN_URL,
            {"username": "alice_dev", "password": "wrong-password-99"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["detail"], "Invalid username or password.")
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    def test_login_unknown_user_returns_same_message(self):
        """(7b) Unknown username -> identical 401 message as wrong password."""
        response = self.client.post(
            LOGIN_URL,
            {"username": "ghost_user", "password": "whatever-123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["detail"], "Invalid username or password.")


class MeAndLogoutTests(APITestCase):
    """Spec items 8-11: protected endpoint + session lifecycle."""

    def setUp(self):
        self.client = APIClient()
        User.objects.create_user(
            username="alice_dev", email="alice@example.com", password=VALID_PASSWORD
        )

    def _login(self):
        return self.client.post(
            LOGIN_URL,
            {"username": "alice_dev", "password": VALID_PASSWORD},
            format="json",
        )

    def test_me_authenticated(self):
        """(8) Logged in -> 200 with id/username/email/metadata, no secrets."""
        self._login()

        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "alice_dev")
        self.assertEqual(response.data["email"], "alice@example.com")
        self.assertIn("id", response.data)
        self.assertIn("date_joined", response.data)
        self.assertNotIn("password", response.data)

    def test_me_unauthenticated(self):
        """(9) No session -> 403 (DRF's default when no WWW-Authenticate)."""
        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_success(self):
        """(10) Logged in -> logout -> 200 and session destroyed."""
        self._login()
        self.assertIsNotNone(self.client.session.session_key)

        response = self.client.post(LOGOUT_URL, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "Logged out.")
        # Session no longer resolves to a user.
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    def test_me_blocked_after_logout(self):
        """(11) Protected endpoint rejects the client after logout."""
        self._login()
        self.assertEqual(self.client.get(ME_URL).status_code, status.HTTP_200_OK)

        self.client.post(LOGOUT_URL, {}, format="json")

        response = self.client.get(ME_URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CSRFProtectionTests(APITestCase):
    """Spec security item: CSRF must NOT be disabled for session auth."""

    def setUp(self):
        # enforce_csrf_checks=True -> Django/DRF really enforce CSRF
        self.client = APIClient(enforce_csrf_checks=True)
        User.objects.create_user(
            username="alice_dev", email="alice@example.com", password=VALID_PASSWORD
        )

    def test_logout_without_csrf_token_rejected(self):
        """Authenticated POST with no CSRF token -> 403."""
        self.client.login(username="alice_dev", password=VALID_PASSWORD)

        response = self.client.post(LOGOUT_URL, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_with_csrf_token_succeeds(self):
        """Cookie + X-CSRFToken header -> 200 (the legitimate path)."""
        self.client.login(username="alice_dev", password=VALID_PASSWORD)

        # Obtain the CSRF cookie the way Postman/React would.
        csrf_response = self.client.get(CSRF_URL)
        self.assertEqual(csrf_response.status_code, status.HTTP_200_OK)
        token = self.client.cookies["csrftoken"].value

        response = self.client.post(
            LOGOUT_URL, {}, format="json", HTTP_X_CSRFTOKEN=token
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
