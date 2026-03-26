"""File analyzer tool for extracting structure and insights from code files.

This tool helps agents quickly understand code files without reading them entirely,
extracting key information like functions, classes, imports, and statistics.
"""

import ast
import re
from typing import Any


def _analyze_python(content: str) -> dict[str, Any]:
    """Analyze a Python file using AST parsing.

    Args:
        content: The Python source code content

    Returns:
        Dictionary with extracted Python-specific information
    """
    result = {
        "functions": [],
        "classes": [],
        "imports": [],
        "variables": [],
        "decorators": [],
        "docstring": None,
        "line_count": len(content.splitlines()),
    }

    try:
        tree = ast.parse(content)

        # Extract module docstring
        if tree.body and isinstance(tree.body[0], ast.Expr):
            if isinstance(tree.body[0].value, ast.Constant) and isinstance(
                tree.body[0].value.value, str
            ):
                result["docstring"] = ast.literal_eval(tree.body[0].value.value[:200] + "...")

        for node in ast.walk(tree):
            # Function definitions
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "args": [arg.arg for arg in node.args.args],
                    "decorators": [
                        d.id if isinstance(d, ast.Name) else ast.unparse(d)
                        for d in node.decorator_list
                    ],
                    "docstring": ast.get_docstring(node)[:100] + "..."
                    if ast.get_docstring(node)
                    else None,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                }
                result["functions"].append(func_info)

            # Class definitions
            elif isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "bases": [ast.unparse(base) for base in node.bases],
                    "decorators": [
                        d.id if isinstance(d, ast.Name) else ast.unparse(d)
                        for d in node.decorator_list
                    ],
                    "docstring": ast.get_docstring(node)[:100] + "..."
                    if ast.get_docstring(node)
                    else None,
                    "methods": [
                        n.name for n in node.body if isinstance(n, ast.FunctionDef)
                    ],
                }
                result["classes"].append(class_info)

            # Import statements
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append({"module": alias.name, "alias": alias.asname})

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    result["imports"].append(
                        {"module": module, "name": alias.name, "alias": alias.asname}
                    )

            # Top-level variable assignments
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        result["variables"].append({"name": target.id, "line": node.lineno})

    except SyntaxError:
        result["parse_error"] = "Invalid Python syntax"

    return result


def _analyze_javascript_typescript(content: str, is_typescript: bool = False) -> dict[str, Any]:
    """Analyze JavaScript/TypeScript file using regex patterns.

    This is a simpler analysis than AST parsing, but works for JS/TS files.

    Args:
        content: The JavaScript/TypeScript source code content
        is_typescript: Whether the file is TypeScript

    Returns:
        Dictionary with extracted JS/TS-specific information
    """
    result = {
        "functions": [],
        "classes": [],
        "imports": [],
        "exports": [],
        "interfaces": [] if is_typescript else None,
        "types": [] if is_typescript else None,
        "line_count": len(content.splitlines()),
    }

    # Import statements
    import_pattern = r'import\s+(?:(\{[^}]+\})|(\w+)(?:\s*,\s*\{[^}]+\})?)\s*from\s*[\'"]([^\'"]+)[\'"]'
    for match in re.finditer(import_pattern, content):
        result["imports"].append({
            "what": match.group(1) or match.group(2),
            "from": match.group(3),
        })

    # Export statements
    export_pattern = r'export\s+(?:default\s+)?(?:(class|function|const|let|var|interface|type)\s+)?(\w+)'
    for match in re.finditer(export_pattern, content):
        result["exports"].append({"type": match.group(1), "name": match.group(2)})

    # Function declarations
    func_pattern = r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)'
    for match in re.finditer(func_pattern, content):
        result["functions"].append({
            "name": match.group(1),
            "params": match.group(2).strip() if match.group(2) else "",
        })

    # Arrow functions
    arrow_pattern = r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>'
    for match in re.finditer(arrow_pattern, content):
        result["functions"].append({"name": match.group(1), "type": "arrow"})

    # Class declarations
    class_pattern = r'(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?'
    for match in re.finditer(class_pattern, content):
        class_info = {"name": match.group(1)}
        if match.group(2):
            class_info["extends"] = match.group(2)
        result["classes"].append(class_info)

    # TypeScript interfaces and types
    if is_typescript:
        interface_pattern = r'(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+(\w+))?'
        for match in re.finditer(interface_pattern, content):
            interface_info = {"name": match.group(1)}
            if match.group(2):
                interface_info["extends"] = match.group(2)
            result["interfaces"].append(interface_info)

        type_pattern = r'(?:export\s+)?type\s+(\w+)\s*='
        for match in re.finditer(type_pattern, content):
            result["types"].append({"name": match.group(1)})

    return result


def _count_code_lines(content: str, comment_prefixes: list[str]) -> dict[str, int]:
    """Count code lines, comment lines, and blank lines.

    Args:
        content: The file content
        comment_prefixes: List of comment prefixes for this language

    Returns:
        Dictionary with line counts
    """
    lines = content.splitlines()
    code_lines = 0
    comment_lines = 0
    blank_lines = 0
    in_multiline_comment = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            blank_lines += 1
            continue

        # Handle multi-line comments (Python docstrings, JS block comments)
        if '"""' in stripped or "'''" in stripped or '/*' in stripped or '*/' in stripped:
            in_multiline_comment = not in_multiline_comment
            comment_lines += 1
            continue

        if in_multiline_comment:
            comment_lines += 1
            continue

        # Single-line comments
        is_comment = any(stripped.startswith(prefix) for prefix in comment_prefixes)
        if is_comment:
            comment_lines += 1
        else:
            code_lines += 1

    return {
        "code_lines": code_lines,
        "comment_lines": comment_lines,
        "blank_lines": blank_lines,
        "total_lines": len(lines),
    }


def file_analyzer(
    file_path: str,
    content: str | None = None,
    analysis_type: str = "full",
) -> dict[str, Any]:
    """Analyze a code file to extract structure, symbols, and statistics.

    This tool helps you quickly understand code files without reading them entirely.
    It extracts key information like functions, classes, imports, and provides
    line statistics. Useful for understanding codebase structure before making changes.

    **When to use:**
    - Before modifying a file you're unfamiliar with
    - To quickly find all functions or classes in a file
    - To understand import dependencies
    - To get code statistics (lines of code, comments, etc.)

    **Supported languages:**
    - Python (.py)
    - JavaScript (.js, .jsx, .mjs, .cjs)
    - TypeScript (.ts, .tsx)

    Args:
        file_path: Path to the file to analyze (relative or absolute)
        content: Optional file content. If not provided, the file will be read.
            Useful when you already have the content in memory.
        analysis_type: Type of analysis to perform:
            - "full": Complete analysis (default)
            - "structure": Only functions, classes, imports (faster)
            - "stats": Only line statistics

    Returns:
        Dictionary containing:
        - file_path: The analyzed file path
        - language: Detected language
        - analysis_type: Type of analysis performed
        - structure: Functions, classes, imports (if "full" or "structure")
        - stats: Line counts (if "full" or "stats")
        - summary: Human-readable summary of the file

    Example:
        >>> result = file_analyzer("src/main.py")
        >>> print(result["summary"])
        "Python file with 3 classes, 12 functions, 5 imports. 150 lines of code."
    """
    # Determine language from file extension
    extension_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
    }

    file_ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    language = extension_map.get(file_ext, "unknown")

    if language == "unknown":
        return {
            "error": f"Unsupported file type: {file_ext}. Supported: .py, .js, .jsx, .ts, .tsx",
            "file_path": file_path,
        }

    # Content should be provided by the agent's file reading capability
    # This is a placeholder - in practice, the agent reads files first
    if content is None:
        return {
            "error": "File content must be provided. Read the file first using read_file tool.",
            "file_path": file_path,
            "hint": "Use read_file to get content, then pass it to this tool.",
        }

    result: dict[str, Any] = {
        "file_path": file_path,
        "language": language,
        "analysis_type": analysis_type,
    }

    # Perform analysis based on language and type
    if analysis_type in ("full", "structure"):
        if language == "python":
            structure = _analyze_python(content)
            comment_prefixes = ["#", '"""', "'''"]
        else:
            is_typescript = language == "typescript"
            structure = _analyze_javascript_typescript(content, is_typescript)
            comment_prefixes = ["//", "/*", "*/"]

        result["structure"] = structure

    if analysis_type in ("full", "stats"):
        if language == "python":
            comment_prefixes = ["#"]
        else:
            comment_prefixes = ["//"]

        stats = _count_code_lines(content, comment_prefixes)
        result["stats"] = stats

    # Generate human-readable summary
    summary_parts = [f"{language.capitalize()} file"]

    if "structure" in result:
        structure = result["structure"]
        if classes := structure.get("classes", []):
            summary_parts.append(f"{len(classes)} classes")
        if functions := structure.get("functions", []):
            summary_parts.append(f"{len(functions)} functions")
        if imports := structure.get("imports", []):
            summary_parts.append(f"{len(imports)} imports")

    if "stats" in result:
        stats = result["stats"]
        summary_parts.append(f"{stats['code_lines']} lines of code")

    result["summary"] = ". ".join(summary_parts) + "."

    return result


def directory_summary(
    file_paths: list[str],
    max_depth: int = 2,
) -> dict[str, Any]:
    """Generate a summary of a directory's code structure.

    This tool helps you understand a directory's overall code organization
    without reading every file individually. It groups files by type and
    provides counts of key structures.

    **When to use:**
    - Exploring a new codebase
    - Planning refactoring work
    - Understanding project structure before making changes

    Args:
        file_paths: List of file paths to analyze (typically from glob or ls)
        max_depth: Maximum directory depth to show in tree structure (default: 2)

    Returns:
        Dictionary containing:
        - total_files: Total number of files
        - by_language: Counts and files grouped by language
        - by_directory: Files grouped by directory
        - summary: Human-readable summary

    Example:
        >>> files = glob("**/*.py")
        >>> result = directory_summary(files)
        >>> print(result["summary"])
        "35 files across 8 directories. Python: 28 files, JavaScript: 7 files."
    """
    if not file_paths:
        return {
            "error": "No file paths provided",
            "hint": "Use glob or ls to get a list of files first.",
        }

    extension_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".txt": "text",
        ".html": "html",
        ".css": "css",
        ".scss": "css",
    }

    by_language: dict[str, dict[str, Any]] = {}
    by_directory: dict[str, list[str]] = {}

    for file_path in file_paths:
        # Get file extension
        file_ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        language = extension_map.get(file_ext, "other")

        # Group by language
        if language not in by_language:
            by_language[language] = {"count": 0, "files": []}
        by_language[language]["count"] += 1
        by_language[language]["files"].append(file_path)

        # Group by directory
        parts = file_path.split("/")
        if len(parts) > 1:
            # Create directory path with limited depth
            dir_path = "/".join(parts[:-1])
            if dir_path not in by_directory:
                by_directory[dir_path] = []
            by_directory[dir_path].append(parts[-1])
        else:
            if "." not in by_directory:
                by_directory["."] = []
            by_directory["."].append(file_path)

    # Generate summary
    lang_summaries = []
    for lang, data in sorted(by_language.items(), key=lambda x: x[1]["count"], reverse=True):
        lang_summaries.append(f"{lang}: {data['count']} files")

    summary = f"{len(file_paths)} files across {len(by_directory)} directories. {'. '.join(lang_summaries)}."

    return {
        "total_files": len(file_paths),
        "by_language": by_language,
        "by_directory": by_directory,
        "summary": summary,
    }