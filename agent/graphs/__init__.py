"""Graph factories — one module per entrypoint in ``langgraph.json``.

The warning filters live here rather than in each factory: importing any graph
initializes this package first, so they are installed before langchain and
deepagents are imported and start emitting them.
"""

import warnings

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
# langchain still ships Pydantic v1 compatibility shims, which warn on 3.14+.
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

__all__: list[str] = []
