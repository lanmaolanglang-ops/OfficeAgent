"""用户 Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.user import User


class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session):
        super().__init__(session, User)

    def get_by_username(self, username: str) -> Optional[User]:
        return self.find_one(username=username)

    def get_by_email(self, email: str) -> Optional[User]:
        return self.find_one(email=email)

    def list_active(self, offset: int = 0, limit: int = 100) -> List[User]:
        return self.find(offset=offset, limit=limit, is_active=True)

    def create_user(self, username: str, email: str = None,
                    password_hash: str = None, display_name: str = None,
                    role: str = "user") -> User:
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            display_name=display_name or username,
            role=role,
        )
        return self.create(user)

    def update_last_login(self, user_id: str):
        from ..time import utc_now
        self.update(user_id, {"last_login": utc_now()})

    def set_preferences(self, user_id: str, prefs_json: str):
        self.update(user_id, {"preferences": prefs_json})

    def deactivate(self, user_id: str):
        self.update(user_id, {"is_active": False})
