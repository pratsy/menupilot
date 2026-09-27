from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "osm:node:123456789"
    name: Mapped[str] = mapped_column(String, index=True)
    city: Mapped[str] = mapped_column(String, index=True)
    address: Mapped[str] = mapped_column(String, default="")
    osm_url: Mapped[str] = mapped_column(String, default="")
    official_website: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    price: Mapped[str | None] = mapped_column(String, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)  # populated once reviews are fetched
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    categories: Mapped[str] = mapped_column(String, default="")  # comma separated (from OSM cuisine tag)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    last_synced_osm_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scraped_menu_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # set only on genuine success
    menu_website_checked: Mapped[bool] = mapped_column(default=False)
    menu_attempt_failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_attempt_failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    menu_items: Mapped[list["MenuItem"]] = relationship(back_populates="restaurant", cascade="all, delete-orphan")
    reviews: Mapped[list["Review"]] = relationship(back_populates="restaurant", cascade="all, delete-orphan")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)

    original_name: Mapped[str] = mapped_column(String, default="")
    original_description: Mapped[str] = mapped_column(Text, default="")
    translated_name: Mapped[str] = mapped_column(String, default="")
    translated_description: Mapped[str] = mapped_column(Text, default="")

    ingredients: Mapped[str] = mapped_column(Text, default="")  # comma separated, English
    ingredients_source: Mapped[str] = mapped_column(String, default="menu_stated")  # menu_stated | llm_inferred

    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, default="food")  # food | drink

    source_url: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    restaurant: Mapped["Restaurant"] = relationship(back_populates="menu_items")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id"), index=True)

    source: Mapped[str] = mapped_column(String, default="website_testimonial")
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    original_text: Mapped[str] = mapped_column(Text, default="")
    original_language: Mapped[str | None] = mapped_column(String, nullable=True)
    translated_text: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[str | None] = mapped_column(String, nullable=True)  # positive | negative | mixed

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    restaurant: Mapped["Restaurant"] = relationship(back_populates="reviews")
