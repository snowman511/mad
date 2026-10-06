"""mad — Multi-Agent Discussion framework.

Shared blackboard + forced adversarial review + objective verification gates.
"""

from mad.models import Message, Tag, RoleSpec, SessionConfig
from mad.blackboard import Blackboard
from mad.agents import Agent, RoleAgent
from mad.orchestrator import Orchestrator, RoundResult
from mad.verifier import Verifier, VerificationResult, ScriptVerifier
from mad.runtime import LLMRuntime, MockRuntime, OpenAICompatibleRuntime, RuntimeStopped

__all__ = [
    "Message",
    "Tag",
    "RoleSpec",
    "SessionConfig",
    "Blackboard",
    "Agent",
    "RoleAgent",
    "Orchestrator",
    "RoundResult",
    "Verifier",
    "VerificationResult",
    "ScriptVerifier",
    "LLMRuntime",
    "MockRuntime",
    "OpenAICompatibleRuntime",
    "RuntimeStopped",
]

__version__ = "0.1.0"
