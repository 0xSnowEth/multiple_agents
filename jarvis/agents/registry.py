from agents.caption_agent import CaptionSpoke
from agents.approval_agent import ApprovalSpoke
from agents.payment_agent import PaymentSpoke
from agents.lead_agent import LeadSpoke
from agents.strategy_agent import StrategySpoke
from agents.posting_agent import PostingSpoke
from agents.onboarding_agent import OnboardingSpoke

AGENT_REGISTRY = [
    CaptionSpoke(),
    ApprovalSpoke(),
    PaymentSpoke(),
    LeadSpoke(),
    StrategySpoke(),
    PostingSpoke(),
    OnboardingSpoke(),
]
