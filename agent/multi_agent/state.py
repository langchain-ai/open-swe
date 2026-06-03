from typing import TypedDict, List, Dict, Any
from langchain_core.messages import BaseMessage

class MultiAgentState(TypedDict):
    """The state of the multi-agent coordination workflow in LangGraph."""
    # Standard message list representing chat history
    messages: List[BaseMessage]
    
    # Original user request / issue description
    task_description: str
    
    # PM Node output: test plans and requirements breakdown
    test_plan: str
    
    # Architect Node output: exact files/symbols to focus on
    target_files: List[str]
    
    # Coding Node output: git diff of changes
    modified_diffs: str
    
    # QA Node output: test runs execution feedback
    test_results: Dict[str, Any]
    
    # Metadata and configuration settings
    config: Dict[str, Any]
