from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True, index=True)

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
