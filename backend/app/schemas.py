from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str


class MenuItemLLM(BaseModel):
    original_name: str = ""
    original_description: str = ""
    translated_name: str
    translated_description: str = ""
    ingredients: list[str] = []
    ingredients_source: str = "menu_stated"  # menu_stated | llm_inferred
    price: float | None = None
    currency: str | None = None
    category: str = "food"  # food | drink


class MenuExtractionResult(BaseModel):
    items: list[MenuItemLLM] = []


class TestimonialItem(BaseModel):
    original_text: str
    translated_text: str
    original_language: str | None = None
    author: str | None = None
    sentiment: str = "unknown"  # positive | negative | mixed | unknown


class TestimonialExtractionResult(BaseModel):
    items: list[TestimonialItem] = []
