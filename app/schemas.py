from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CAMPAIGN_PREFIX = "test_kozina_"

# ISO 3166-1 alpha-2. Значения не зависят от языка интерфейса.
COUNTRY_CODES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ "
    "BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR "
    "CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR "
    "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU "
    "ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ "
    "LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ "
    "MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF "
    "PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI "
    "SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR "
    "TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split()
)


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


class CampaignCreate(CampaignName):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    country_code: str
    offer_id: int = Field(gt=0, strict=True)
    domain_id: int = Field(gt=0, strict=True)
    group_id: int = Field(gt=0, strict=True)
    traffic_source_id: int = Field(gt=0, strict=True)

    @field_validator("country_code")
    @classmethod
    def validate_country(cls, value):
        value = value.strip().upper()
        if value not in COUNTRY_CODES:
            raise ValueError("Укажите код страны ISO 3166-1 alpha-2, например AU")
        return value


class CreationResult(BaseModel):
    request_id: str
    status: str
    campaign_id: int | None = None
    campaign_name: str
    campaign_url: str | None = None
    admin_url: str | None = None
    stream_ids: list[int] = Field(default_factory=list)
    error_code: str | None = None
    message: str
