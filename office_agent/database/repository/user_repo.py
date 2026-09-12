"""用户 Repository"""
import logging
from typing import Optional, List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.user import User
from ...security.auth.password import hash_password, verify_password_and_rehash


logger = logging.getLogger(__name__)


class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session):
        super().__init__(session, User)

    def get_by_username(self, username: str) -> Optional[User]:
        return self.find_one(username=username)

    def get_by_email(self, email: str) -> Optional[User]:
        return self.find_one(email=email)

    def list_active(self, offset: int = 0, limit: int = 100) -> List[User]:
        return self.find(offset=offset, limit=limit, is_active=True)

    def create_user(self, username: str, email: str | None = None,
                    password: str | None = None, display_name: str | None = None,
                    role: str = "user") -> User:
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password) if password is not None else None,
            display_name=display_name or username,
            role=role,
        )
        return self.create(user)

    def set_password(self, user_id: str, password: str) -> Optional[User]:
        """Set/change/reset a password through the canonical hasher."""
        return self.update(user_id, {"password_hash": hash_password(password)})

    def authenticate_password(self, username: str, password: str) -> Optional[User]:
        """Verify credentials and opportunistically upgrade a legacy hash.

        Repository methods follow the existing unit-of-work convention: the
        caller owns the outer commit. A savepoint contains upgrade flush
        failures so a correct credential does not become a password error.
        """
        user = self.get_by_username(username)
        if user is None or not user.is_active or not user.password_hash:
            return None
        verification = verify_password_and_rehash(password, user.password_hash)
        if not verification.valid:
            return None
        if verification.replacement_hash is None:
            return user

        previous_hash = user.password_hash
        try:
            with self.session.begin_nested():
                user.password_hash = verification.replacement_hash
                self.session.flush([user])
        except SQLAlchemyError as exc:
            user.password_hash = previous_hash
            logger.warning(
                "用户密码哈希升级落库失败，将在下次成功登录时重试: "
                "user_id=%s error=%s",
                user.id,
                type(exc).__name__,
            )
        return user

    def update_last_login(self, user_id: str):
        from ..time import utc_now
        self.update(user_id, {"last_login": utc_now()})

    def set_preferences(self, user_id: str, prefs_json: str):
        self.update(user_id, {"preferences": prefs_json})

    def deactivate(self, user_id: str):
        self.update(user_id, {"is_active": False})
