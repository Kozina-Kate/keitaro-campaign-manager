from pydantic import BaseModel, Field, field_validator

CAMPAIGN_PREFIX = "test_kozina_"


class CampaignName(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(CAMPAIGN_PREFIX):
            raise ValueError(f"Название должно начинаться с {CAMPAIGN_PREFIX}")
        if not value[len(CAMPAIGN_PREFIX) :].strip():
            raise ValueError("После префикса нужно указать название кампании")
        return value


class ReferenceItem(BaseModel):
    id: int
    name: str


class ReferenceData(BaseModel):
    domains: list[ReferenceItem]
    groups: list[ReferenceItem]
    traffic_sources: list[ReferenceItem]
    offers: list[ReferenceItem]
