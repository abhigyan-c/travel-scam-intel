from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.hypothesis_resolution import Decision


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "MERGE"},
        {"confidence": 1.1},
        {"confidence": float("nan")},
        {"ambiguous": "false"},
        {"reason": " "},
        {"extra": "ignored"},
        {"decision": "NEW", "target_id": str(uuid4())},
        {"target_id": None},
    ],
)
def test_invalid_decisions_fail_closed(changes):
    with pytest.raises(ValidationError):
        Decision.model_validate(
            {
                "decision": "SAME",
                "target_id": str(uuid4()),
                "confidence": 0.95,
                "ambiguous": False,
                "reason": "Same mechanism",
                **changes,
            }
        )
