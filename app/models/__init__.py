from app.models.claim import Claim
from app.models.claim_extraction import ClaimExtraction
from app.models.document import Document
from app.models.duplicate_detection import DuplicateAssessment, DuplicateDetectionRun
from app.models.duplicate_group import DuplicateGroup
from app.models.evidence import Evidence
from app.models.hypothesis import Hypothesis
from app.models.hypothesis_lifecycle import HypothesisLifecycleEvent
from app.models.hypothesis_relation import HypothesisRelation
from app.models.hypothesis_resolution import HypothesisResolution
from app.models.independence_run import IndependenceRun
from app.models.location import Location, LocationAlias
from app.models.research_job import ResearchJob
from app.models.score_snapshot import HypothesisScoreSnapshot
from app.models.source import Source
from app.models.user_report import UserReport

__all__ = [
    "Claim",
    "ClaimExtraction",
    "Document",
    "DuplicateGroup",
    "DuplicateAssessment",
    "DuplicateDetectionRun",
    "Evidence",
    "Hypothesis",
    "HypothesisLifecycleEvent",
    "HypothesisRelation",
    "HypothesisResolution",
    "HypothesisScoreSnapshot",
    "Location",
    "IndependenceRun",
    "LocationAlias",
    "ResearchJob",
    "Source",
    "UserReport",
]
