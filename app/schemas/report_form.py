from typing import Literal
from uuid import UUID

from app.schemas.intelligence import Contract, LocationRef


class ReportForm(Contract):
    contract_version: Literal["report-form-v1"] = "report-form-v1"
    form_id: UUID
    location: LocationRef | None = None
