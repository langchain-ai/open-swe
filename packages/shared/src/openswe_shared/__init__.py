"""What Open SWE's backend and its Managed Deep Agents runtime both build runs from.

The backend and ``mda/`` never import each other. Code either side may need to
shape a run lives here instead: the model-facing prompts and their renderer
(``prompting``), the ``<input-message>`` envelope (``input_messages``), and
fetching the images a message links to without reaching a private address
(``url_safety``, ``images``).
"""
