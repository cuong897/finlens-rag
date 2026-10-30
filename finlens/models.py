from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Scope = Literal["consolidated", "standalone"]
Dataset = Literal["demo", "real", "all"]

METRICS = {
    "revenue": "Doanh thu thuần", "net_income": "Lợi nhuận sau thuế",
    "total_assets": "Tổng tài sản", "liabilities": "Nợ phải trả",
    "operating_cash_flow": "Dòng tiền kinh doanh", "gross_profit": "Lợi nhuận gộp",
    "equity": "Vốn chủ sở hữu", "cash": "Tiền và tương đương tiền",
    "receivables": "Phải thu khách hàng", "inventory": "Hàng tồn kho",
    "current_assets": "Tài sản ngắn hạn", "current_liabilities": "Nợ ngắn hạn",
    "interest_expense": "Chi phí lãi vay",
}
UNITS = {"one": "1", "thousand": "1000", "million": "1000000", "billion": "1000000000"}
UNIT_LABELS = {"one": "đơn vị", "thousand": "nghìn", "million": "triệu", "billion": "tỷ"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AskRequest(StrictModel):
    question: str = Field(min_length=3, max_length=1200)
    company: str = Field(min_length=1, max_length=120)
    years: list[int] = Field(default_factory=list, max_length=10)
    scope: Scope = "consolidated"
    dataset: Dataset = "real"
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("years")
    @classmethod
    def validate_years(cls, years):
        if any(not 1990 <= year <= 2100 for year in years):
            raise ValueError("Năm phải nằm trong khoảng 1990–2100.")
        return sorted(set(years))


class CompareRequest(StrictModel):
    company: str = Field(min_length=1, max_length=120)
    metric: str
    from_year: int = Field(ge=1990, le=2100)
    to_year: int = Field(ge=1990, le=2100)
    scope: Scope = "consolidated"
    dataset: Dataset = "real"

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, value):
        if value not in METRICS:
            raise ValueError("Chỉ tiêu không được hỗ trợ.")
        return value


class ImportURLRequest(StrictModel):
    url: str = Field(min_length=8, max_length=2048)
    company: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=1990, le=2100)
    scope: Scope = "consolidated"


class FactRequest(StrictModel):
    document_id: str = Field(min_length=1, max_length=64)
    page: int = Field(ge=1, le=250)
    metric: str
    year: int = Field(ge=1990, le=2100)
    value: str = Field(pattern=r"^-?\d{1,24}(?:\.\d{1,8})?$")
    unit: Literal["one", "thousand", "million", "billion"]
    currency: Literal["VND", "USD", "EUR"] = "VND"
    confirmed: Literal[True]
    note: str = Field(min_length=10, max_length=600)

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, value):
        return CompareRequest.validate_metric(value)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value):
        return format(Decimal(value), "f")
