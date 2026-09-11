"""verascite -- transparent, auditable legal citation verification.

The deterministic core runs with no model in the loop. Every check is
independently runnable from the CLI, and every verdict carries its evidence.
"""

__version__ = "0.3.0"


#: Shown on every surface and in every artifact this tool produces. Kept here so
#: the wording cannot drift between the command line, the reports, the record,
#: the server and the browser surfaces.
DISCLAIMER = "AI can make mistakes. For experimental and research use only."
