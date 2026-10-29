import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Setup import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.database import Base
from models.users import Users
from services.keycloak_auth import (
    verify_keycloak_jwt,
    resolve_keycloak_user,
    extract_token_roles,
    require_role,
    KEYCLOAK_ISSUER,
    KEYCLOAK_CLIENT_ID,
)

# In-memory SQLite for testing resolve_keycloak_user
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class TestKeycloakSSO(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=engine)

    # ── TEST 1: Token Valid dengan Role Benar ─────────────────────────────────
    def test_token_valid_with_correct_role(self):
        mock_payload = {
            "sub": "sub-1234",
            "iss": KEYCLOAK_ISSUER,
            "azp": KEYCLOAK_CLIENT_ID,
            "preferred_username": "john_doe",
            "email": "john@example.com",
            "email_verified": True,
            "resource_access": {
                KEYCLOAK_CLIENT_ID: {"roles": ["user"]}
            },
            "exp": int(time.time()) + 3600,
            "nbf": int(time.time()) - 10,
        }

        with patch("services.keycloak_auth.get_jwks_client") as mock_jwks, \
             patch("jwt.decode", return_value=mock_payload):
            mock_key = MagicMock()
            mock_key.key = "fake_pub_key"
            mock_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key

            payload = verify_keycloak_jwt("fake.jwt.token")
            self.assertEqual(payload["sub"], "sub-1234")
            self.assertIn("user", payload["_extracted_roles"])

    # ── TEST 2: Token Tanpa Role (403 Forbidden) ──────────────────────────────
    def test_token_without_role(self):
        mock_payload = {
            "sub": "sub-no-role",
            "iss": KEYCLOAK_ISSUER,
            "azp": KEYCLOAK_CLIENT_ID,
            "preferred_username": "norole_user",
            "resource_access": {
                KEYCLOAK_CLIENT_ID: {"roles": []}
            },
            "realm_access": {"roles": []},
            "exp": int(time.time()) + 3600,
            "nbf": int(time.time()) - 10,
        }

        with patch("services.keycloak_auth.get_jwks_client") as mock_jwks, \
             patch("jwt.decode", return_value=mock_payload):
            mock_key = MagicMock()
            mock_key.key = "fake_pub_key"
            mock_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key

            with self.assertRaises(HTTPException) as ctx:
                verify_keycloak_jwt("fake.jwt.token")

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertIn("belum memiliki akses", ctx.exception.detail)

    # ── TEST 3: Role dari Client Lain (Bukan VoxaGIS) ─────────────────────────
    def test_token_role_from_other_client(self):
        mock_payload = {
            "sub": "sub-other-client",
            "iss": KEYCLOAK_ISSUER,
            "azp": KEYCLOAK_CLIENT_ID,
            "preferred_username": "other_user",
            "resource_access": {
                "flowgis": {"roles": ["analyst"]},
                "grovia": {"roles": ["user"]}
            },
            "realm_access": {"roles": ["offline_access"]},
            "exp": int(time.time()) + 3600,
            "nbf": int(time.time()) - 10,
        }

        with patch("services.keycloak_auth.get_jwks_client") as mock_jwks, \
             patch("jwt.decode", return_value=mock_payload):
            mock_key = MagicMock()
            mock_key.key = "fake_pub_key"
            mock_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key

            with self.assertRaises(HTTPException) as ctx:
                verify_keycloak_jwt("fake.jwt.token")

            self.assertEqual(ctx.exception.status_code, 403)

    # ── TEST 4: Token Kedaluwarsa (401 Unauthorized) ──────────────────────────
    def test_token_expired(self):
        import jwt as pyjwt
        with patch("services.keycloak_auth.get_jwks_client") as mock_jwks, \
             patch("jwt.decode", side_effect=pyjwt.ExpiredSignatureError("Signature has expired")):
            mock_key = MagicMock()
            mock_key.key = "fake_pub_key"
            mock_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key

            with self.assertRaises(HTTPException) as ctx:
                verify_keycloak_jwt("expired.jwt.token")

            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("kedaluwarsa", ctx.exception.detail.lower())

    # ── TEST 5: Issuer Salah (401 Unauthorized) ───────────────────────────────
    def test_token_invalid_issuer(self):
        import jwt as pyjwt
        with patch("services.keycloak_auth.get_jwks_client") as mock_jwks, \
             patch("jwt.decode", side_effect=pyjwt.InvalidIssuerError("Invalid issuer")):
            mock_key = MagicMock()
            mock_key.key = "fake_pub_key"
            mock_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key

            with self.assertRaises(HTTPException) as ctx:
                verify_keycloak_jwt("invalid.issuer.jwt")

            self.assertEqual(ctx.exception.status_code, 401)

    # ── TEST 6: Pencocokan via Sub ───────────────────────────────────────────
    def test_resolve_user_via_sub(self):
        existing_user = Users(
            keycloak_sub="sub-match-001",
            username="matched_user",
            email="matched@example.com",
            role="user",
            is_verified=True,
        )
        self.db.add(existing_user)
        self.db.commit()

        payload = {
            "sub": "sub-match-001",
            "email": "matched@example.com",
            "preferred_username": "matched_user",
            "_extracted_roles": ["user"]
        }

        resolved = resolve_keycloak_user(self.db, payload)
        self.assertEqual(resolved.id, existing_user.id)
        self.assertEqual(resolved.username, "matched_user")

    # ── TEST 7: Pencocokan via Email (email_verified == True) ─────────────────
    def test_resolve_user_via_email_verified_true(self):
        existing_user = Users(
            keycloak_sub=None,
            username="legacy_user",
            email="legacy@example.com",
            role="user",
            is_verified=True,
        )
        self.db.add(existing_user)
        self.db.commit()

        payload = {
            "sub": "sub-newly-linked",
            "email": "LEGACY@example.com",  # Test case-insensitivity
            "email_verified": True,
            "preferred_username": "legacy_user",
            "_extracted_roles": ["user"]
        }

        with patch("services.keycloak_auth.ensure_user_api_key"):
            resolved = resolve_keycloak_user(self.db, payload)
            self.assertEqual(resolved.id, existing_user.id)
            self.assertEqual(resolved.keycloak_sub, "sub-newly-linked")

    # ── TEST 8: Pencocokan via Email (email_verified == False) ────────────────
    def test_resolve_user_via_email_verified_false(self):
        existing_user = Users(
            keycloak_sub=None,
            username="unverified_user",
            email="unverified@example.com",
            role="user",
            is_verified=False,
        )
        self.db.add(existing_user)
        self.db.commit()

        payload = {
            "sub": "sub-unverified",
            "email": "unverified@example.com",
            "email_verified": False,
            "preferred_username": "new_unverified",
            "_extracted_roles": ["user"]
        }

        with patch("services.keycloak_auth.ensure_user_api_key"):
            # Harus menolak akses (403) jika email ada di db lokal tapi email_verified false
            with self.assertRaises(HTTPException) as ctx:
                resolve_keycloak_user(self.db, payload)

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertIn("Verifikasi email Anda terlebih dahulu", ctx.exception.detail)

    # ── TEST 9: Konflik Email Ganda (Multiple Users Match) ────────────────────
    def test_resolve_user_duplicate_email_conflict(self):
        # Mock query return multi-users for same email
        user1 = Users(id=1, keycloak_sub=None, username="dup1", email="shared@example.com")
        user2 = Users(id=2, keycloak_sub=None, username="dup2", email="shared@example.com")

        payload = {
            "sub": "sub-duplicate-email",
            "email": "shared@example.com",
            "email_verified": True,
            "preferred_username": "dup_candidate",
            "_extracted_roles": ["user"]
        }

        with patch.object(self.db, "query") as mock_query:
            mock_filter = MagicMock()
            mock_filter.first.return_value = None  # no sub match
            mock_filter.all.return_value = [user1, user2]  # multi-user email match
            mock_query.return_value.filter.return_value = mock_filter

            with self.assertRaises(HTTPException) as ctx:
                resolve_keycloak_user(self.db, payload)

            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("beberapa akun", ctx.exception.detail)

    # ── TEST 10: Require Role Dependency ──────────────────────────────────────
    def test_require_role_dependency(self):
        admin_checker = require_role("admin")

        # Admin payload -> diizinkan
        admin_payload = {"_extracted_roles": ["admin", "user"], "preferred_username": "adm"}
        self.assertEqual(admin_checker(admin_payload), admin_payload)

        # Regular user payload -> ditolak 403
        user_payload = {"_extracted_roles": ["user"], "preferred_username": "usr"}
        with self.assertRaises(HTTPException) as ctx:
            admin_checker(user_payload)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("Memerlukan role 'admin'", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
