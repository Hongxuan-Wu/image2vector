"""Turn a bitmap figure into an editable ``.ai`` file.

Three moving parts:

``vc``          drives VectorCraft's headless CLI (trace, expand, export)
``pipeline``    wraps one conversion in a job folder with a JSON report
``inspect_ai``  reads a finished ``.ai`` back through its PDF layer

No Adobe application is involved anywhere in this package.
"""

__version__ = "1.2.1"

from . import inspect_ai, pipeline, vc  # noqa: F401

__all__ = ["inspect_ai", "pipeline", "vc", "__version__"]
