"""P4-4 canonical password hashing and legacy compatibility tests."""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
import hashlib
import importlib
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

import office_agent.database.models  # noqa: F401 - register ORM relationships
from office_agent.database.models.user import User
from office_agent.database.repository.user_repo import UserRepository
from office_agent.database.connection import get_engine
from office_agent.security.auth import password as password_module
from office_agent.security.auth.password import (
    LEGACY_PASSWORD_HASH_ITERATIONS,
    MAX_PASSWORD_LENGTH,
    PASSWORD_HASH_ITERATIONS,
    PASSWORD_HASH_SCHEME,
    hash_password,
    is_password_strong,
    needs_rehash,
    verify_password,
    verify_password_and_rehash,
)


def _legacy_hash(
    password: str,
    *,
    salt: bytes = b"L" * 32,
    iterations: int = LEGACY_PASSWORD_HASH_ITERATIONS,
    algorithm: str = "sha256",
) -> str:
    """Reproduce the exact format emitted by the pre-P4-4 implementation."""
    digest = hashlib.pbkdf2_hmac(
        algorithm,
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )
    return (
        f"pbkdf2_{algorithm}${iterations}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(digest).decode('ascii')}"
    )


class _SessionStub:
    def __init__(self, user: User | None = None, *, fail_flush: bool = False):
        self.user = user
        self.fail_flush = fail_flush
        self.added: list[User] = []
        self.flush_calls = 0

    def add(self, user: User) -> None:
        self.added.append(user)

    def get(self, _model, _user_id):
        return self.user

    def flush(self, _objects=None) -> None:
        self.flush_calls += 1
        if self.fail_flush:
            raise SQLAlchemyError("simulated write failure")

    def begin_nested(self):
        return nullcontext()


def _repo_for(user: User | None = None, *, fail_flush: bool = False):
    session = _SessionStub(user, fail_flush=fail_flush)
    repo = UserRepository(session)  # type: ignore[arg-type]
    if user is not None:
        repo.get_by_username = lambda _username: user  # type: ignore[method-assign]
    return repo, session


@contextmanager
def _recorded_logs(logger_name: str):
    target = logging.getLogger(logger_name)
    records = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _ListHandler()
    old_level = target.level
    old_disabled = target.disabled
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    target.disabled = False
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(old_level)
        target.disabled = old_disabled


def test_new_hash_uses_canonical_self_describing_strategy():
    stored = hash_password("Canonical!9")
    scheme, iterations, salt, digest = stored.split("$")
    assert scheme == PASSWORD_HASH_SCHEME
    assert int(iterations) == PASSWORD_HASH_ITERATIONS == 600_000
    assert len(base64.b64decode(salt, validate=True)) == 32
    assert len(base64.b64decode(digest, validate=True)) == 32
    assert len(stored) < 256


def test_new_hash_is_salted_and_never_contains_plaintext():
    plaintext = "NeverStoreMe!7"
    first = hash_password(plaintext)
    second = hash_password(plaintext)
    assert plaintext not in first
    assert first != second
    assert verify_password(plaintext, first)
    assert verify_password(plaintext, second)


@pytest.mark.parametrize("password", ["正确的密码A9!", "emoji-🔐-Pass9!"])
def test_unicode_password_round_trip_without_normalization(password):
    stored = hash_password(password)
    assert verify_password(password, stored)
    assert not verify_password(password.strip().lower(), stored)


def test_correct_password_passes_and_wrong_password_fails():
    stored = hash_password("Correct-Horse-9!")
    assert verify_password("Correct-Horse-9!", stored)
    assert not verify_password("Correct-Horse-9?", stored)


def test_maximum_new_password_length_round_trip_and_oversize_rejected():
    maximum = "密" * MAX_PASSWORD_LENGTH
    assert verify_password(maximum, hash_password(maximum))
    with pytest.raises(ValueError, match="must not exceed"):
        hash_password(maximum + "密")


def test_empty_password_is_hashable_but_rejected_by_existing_policy():
    stored = hash_password("")
    assert verify_password("", stored)
    assert is_password_strong("")[0] is False


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-password-hash",
        "argon2id$600000$AAAA$AAAA",
        "pbkdf2_sha256$1$" + base64.b64encode(b"S" * 32).decode() + "$"
        + base64.b64encode(b"D" * 32).decode(),
        "pbkdf2_sha256$700000$" + base64.b64encode(b"S" * 32).decode() + "$"
        + base64.b64encode(b"D" * 32).decode(),
        "pbkdf2_sha256$600000$@@@$###",
        "pbkdf2_sha256$600000$" + base64.b64encode(b"short").decode() + "$"
        + base64.b64encode(b"D" * 32).decode(),
    ],
)
def test_malformed_unknown_and_unissued_hashes_fail_closed(stored):
    assert verify_password("anything", stored) is False
    assert needs_rehash(stored) is False
    assert verify_password_and_rehash("anything", stored).replacement_hash is None


def test_previously_accepted_sha1_scheme_is_now_rejected():
    assert verify_password("Legacy!9", _legacy_hash("Legacy!9", algorithm="sha1")) is False


def test_legacy_hash_verifies_and_requests_rehash():
    stored = _legacy_hash("Legacy!9")
    assert verify_password("Legacy!9", stored)
    assert needs_rehash(stored)

    result = verify_password_and_rehash("Legacy!9", stored)
    assert result.valid is True
    assert result.replacement_hash is not None
    assert result.replacement_hash != stored
    assert verify_password("Legacy!9", result.replacement_hash)
    assert not needs_rehash(result.replacement_hash)


def test_wrong_legacy_password_never_requests_rehash():
    stored = _legacy_hash("Legacy!9")
    result = verify_password_and_rehash("wrong", stored)
    assert result.valid is False
    assert result.replacement_hash is None


def test_canonical_hash_does_not_repeat_rehash():
    stored = hash_password("Canonical!9")
    result = verify_password_and_rehash("Canonical!9", stored)
    assert result.valid is True
    assert result.replacement_hash is None
    assert needs_rehash(stored) is False


def test_oversized_legacy_password_still_logs_in_without_forced_rehash():
    password = "A" * (MAX_PASSWORD_LENGTH + 1)
    stored = _legacy_hash(password)
    result = verify_password_and_rehash(password, stored)
    assert result.valid is True
    assert result.replacement_hash is None


def test_create_and_password_change_use_canonical_helper():
    repo, session = _repo_for()
    created = repo.create_user("alice", password="Register!9")
    assert created.password_hash != "Register!9"
    assert verify_password("Register!9", created.password_hash)
    assert not needs_rehash(created.password_hash)

    session.user = created
    changed = repo.set_password(created.id, "Changed!9")
    assert changed is created
    assert verify_password("Changed!9", created.password_hash)
    assert not verify_password("Register!9", created.password_hash)


def test_repository_login_upgrades_legacy_hash_only_after_success():
    user = User(
        id="user-1", username="alice", password_hash=_legacy_hash("Legacy!9"),
        is_active=True,
    )
    repo, _session = _repo_for(user)
    assert repo.authenticate_password("alice", "wrong") is None
    assert needs_rehash(user.password_hash)

    assert repo.authenticate_password("alice", "Legacy!9") is user
    assert verify_password("Legacy!9", user.password_hash)
    assert not needs_rehash(user.password_hash)


def test_repository_upgrade_persists_in_real_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'password-upgrade.db'}")
    User.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    plaintext = "PersistLegacy!9"
    legacy = _legacy_hash(plaintext)

    with factory() as session:
        session.add(User(
            id="user-db", username="persisted", password_hash=legacy,
            is_active=True,
        ))
        session.commit()

    with factory() as session:
        repo = UserRepository(session)
        user = repo.authenticate_password("persisted", plaintext)
        assert user is not None
        assert user.password_hash != legacy
        session.commit()

    with factory() as session:
        stored = session.get(User, "user-db").password_hash
        assert verify_password(plaintext, stored)
        assert not needs_rehash(stored)
    engine.dispose()


def test_upgrade_write_failure_preserves_login_and_legacy_hash():
    plaintext = "LegacyFailure!9"
    legacy = _legacy_hash(plaintext)
    user = User(
        id="user-2", username="bob", password_hash=legacy, is_active=True,
    )
    repo, _session = _repo_for(user, fail_flush=True)

    with _recorded_logs("office_agent.database.repository.user_repo") as records:
        assert repo.authenticate_password("bob", plaintext) is user
    assert user.password_hash == legacy
    assert verify_password(plaintext, user.password_hash)
    messages = [record.getMessage() for record in records]
    assert any("user_id=user-2" in message for message in messages)
    assert all(plaintext not in message and legacy not in message for message in messages)


def test_two_simultaneous_upgrade_results_are_both_safe_last_writers():
    plaintext = "Concurrent!9"
    legacy = _legacy_hash(plaintext)
    first = verify_password_and_rehash(plaintext, legacy).replacement_hash
    second = verify_password_and_rehash(plaintext, legacy).replacement_hash
    assert first is not None and second is not None and first != second
    assert verify_password(plaintext, first)
    assert verify_password(plaintext, second)
    final_hash = second
    assert verify_password(plaintext, final_hash)


def test_logs_never_contain_password_or_complete_hash():
    plaintext = "DoNotLog!9"
    malformed = "pbkdf2_sha256$not-a-number$private-salt$private-digest"
    with _recorded_logs("office_agent.security.auth.password") as records:
        assert verify_password(plaintext, malformed) is False
    messages = [record.getMessage() for record in records]
    assert messages
    assert all(plaintext not in message and malformed not in message for message in messages)


def test_database_echo_hides_complete_password_hash():
    stored = hash_password("EchoSecret!9")
    engine = get_engine("sqlite:///:memory:")
    engine.echo = True
    assert engine.hide_parameters is True
    with _recorded_logs("sqlalchemy.engine.Engine") as records:
        User.__table__.create(engine)
        factory = sessionmaker(bind=engine)
        with factory() as session:
            session.add(User(
                id="echo-user", username="echo", password_hash=stored,
                is_active=True,
            ))
            session.commit()
    messages = [record.getMessage() for record in records]
    assert messages
    assert all(stored not in message and "EchoSecret!9" not in message
               for message in messages)
    engine.dispose()


def test_canonical_hash_survives_module_reload():
    stored = password_module.hash_password("Reload!9")
    reloaded = importlib.reload(password_module)
    assert reloaded.verify_password("Reload!9", stored)
