import argparse
import contextlib
import importlib
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

# Constants
EVALUATION_MODEL = "openai:o3-mini"
MIN_SCORE = 0.0
MAX_SCORE = 1.0
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

# Add parent directory to Python path for module imports
sys.path.append(str(Path(__file__).parent.parent.absolute()))
logging.disable(logging.CRITICAL)



EVALUATION_PROMPT_TEMPLATE = """
You are evaluating the output of an AI system. Please evaluate if the output 
provides a reasonable and coherent response to the input. You must evaluate the output based on the 
ground truth. If the output is not as detailed as the ground truth, you should not give a good score.

Input: {inputs}
Generated output: {outputs}
{ground_truth_section}

Evaluation criteria:
1. Relevance: Does the answer address the input prompt?
2. Completeness: Is it at least as detailed as the ground truth?
3. Accuracy: Are the facts or logic correct? If the ground truth is a number, the output should be a number.
4. Clarity: Is it coherent and well-organized?

Please provide your evaluation as a JSON object with the following fields:
- score: A score between 0 and 1, where 0 means completely fails criteria and 1 means perfectly meets all criteria
- explanation: A brief explanation of your score
"""


class EvaluationResult(BaseModel):
    """Schema for LLM evaluation results."""
    
    score: float = Field(
        description="A score between 0 and 1, where 0 means completely fails criteria and 1 means perfectly meets all criteria",
        ge=MIN_SCORE,
        le=MAX_SCORE
    )
    explanation: str = Field(
        description="A brief explanation of the evaluation score"
    )

def format_user_input(user_input: str) -> Dict[str, Any]:
    return {
        "messages": [HumanMessage(content=user_input)]
    }

def format_output_for_evaluation(output: Any) -> str:
    """
    Format the output in a readable way for LLM evaluation.
    
    Args:
        output: The raw output from the compiled graph
        
    Returns:
        Formatted string representation of the output
    """
    try:
        if isinstance(output, (dict, list)):
            return json.dumps(output, indent=2, ensure_ascii=False)
        return str(output)
    except (TypeError, ValueError):
        return str(output)

def parse_file_path(file_path: str) -> Tuple[str, str]:
    path = Path(file_path)
    script_name = path.name
    
    module_path = str(path.with_suffix(''))
    normalized_path = module_path.replace('/', '.').replace('\\', '.')
    
    return normalized_path, script_name

def load_compiled_graph(file_path: str) -> Any:
    """
    Load and return the compiled graph from the specified module.
    
    Args:
        file_path: Path to the Python module containing the graph
        
    Returns:
        The compiled graph object ready for invocation
    """
    try:
        module_path, script_name = parse_file_path(file_path)
        module = importlib.import_module(module_path)
        
        for attr_name in ['compiled_graph', 'graph', 'app']:
            if hasattr(module, attr_name):
                graph = getattr(module, attr_name)
                return graph
        
        # No graph found
        available_attrs = [attr for attr in dir(module) if not attr.startswith('_')]
        raise AttributeError(
            f"No 'compiled_graph' or 'graph' found in {file_path}. "
            f"Available attributes: {available_attrs}"
        )
        
    except ImportError as e:
        raise ImportError(f"Could not import module {module_path}: {e}")
    except Exception as e:
        raise Exception(f"Error loading graph from {file_path}: {e}")

def evaluate_script(
    file_path: str, 
    user_input: str, 
    ground_truth: Optional[str] = None
) -> Tuple[float, str]:
    """
    Evaluate a script's performance on given input using an LLM judge.
    
    Args:
        file_path: Path to the script file to evaluate
        user_input: User input/question to test the script with
        ground_truth: Optional expected answer for comparison
        
    Returns:
        Tuple of (score, explanation) where score is 0.0-1.0
    """
    try:
        input_data = format_user_input(user_input)
        
        # Capture all stdout and stderr during both import and invoke
        buf = io.StringIO()
        err_buf = io.StringIO()
        
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err_buf):
            compiled_graph = load_compiled_graph(file_path)
            output = compiled_graph.invoke(input_data)
        
        # Format output for better readability in evaluation
        formatted_output = format_output_for_evaluation(output)
        
        # Proceed to LLM evaluation un-redirected
        ground_truth_section = (
            f"Expected answer/ground truth: {ground_truth}" 
            if ground_truth else ""
        )
        
        # Evaluate with LLM
        evaluator = init_chat_model(EVALUATION_MODEL).with_structured_output(EvaluationResult)
        
        evaluation_prompt = EVALUATION_PROMPT_TEMPLATE.format(
            inputs=input_data,
            outputs=formatted_output,
            ground_truth_section=ground_truth_section
        )
        
        eval_result = evaluator.invoke(evaluation_prompt)
        
        # Ensure we have an EvaluationResult instance
        if not isinstance(eval_result, EvaluationResult):
            raise TypeError(f"Expected EvaluationResult, got {type(eval_result)}")
        
        return eval_result.score, eval_result.explanation
        
    except Exception as e:
        error_msg = f"Evaluation failed: {str(e)}"
        return MIN_SCORE, error_msg

def run_evaluation(
    file_path: str, 
    user_input: str, 
    ground_truth: Optional[str] = None
) -> None:
    """
    Run evaluation and output results to stdout.
    
    Args:
        file_path: Path to the script file to evaluate
        user_input: User input/question to test the script with
        ground_truth: Optional expected answer for comparison
    """
    try:
        score, explanation = evaluate_script(file_path, user_input, ground_truth)
        
        print(f"{score:.2f}")
        print(explanation)
        
    except Exception as e:
        print(f"{MIN_SCORE:.2f}")
        print(f"Critical error: {str(e)}")

def create_argument_parser() -> argparse.ArgumentParser:
    """
    Create and configure the command-line argument parser.
    
    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(
        description="Evaluate a single LLM experiment script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "file_path",
        help="Path to the script file (e.g., expts/custom/agent.py)"
    )
    
    parser.add_argument(
        "--user-input", "-i",
        required=True,
        help="User input/question as a simple string (e.g., 'What is 2+2?')"
    )
    
    parser.add_argument(
        "--ground-truth", "-g",
        help="Expected answer/ground truth for comparison"
    )
    
    return parser

def main() -> int:
    """
    Main entry point for the evaluation script.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    parser = create_argument_parser()
    args = parser.parse_args()
    
    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"{MIN_SCORE:.2f}")
        print(f"Error: File not found: {args.file_path}")
        return EXIT_FAILURE
    
    if not args.user_input.strip():
        print(f"{MIN_SCORE:.2f}")
        print("Error: User input cannot be empty")
        return EXIT_FAILURE
    
    try:
        run_evaluation(
            file_path=args.file_path,
            user_input=args.user_input,
            ground_truth=args.ground_truth
        )
        return EXIT_SUCCESS
        
    except KeyboardInterrupt:
        return EXIT_FAILURE
    except Exception as e:
        print(f"{MIN_SCORE:.2f}")
        print(f"Unexpected error: {str(e)}")
        return EXIT_FAILURE

if __name__ == "__main__":
    sys.exit(main())