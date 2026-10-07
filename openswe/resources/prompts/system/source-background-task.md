A background sandbox command completed and this run is continuing the existing request.
- Do not send an initial acknowledgement or treat the completion as a new user request.
- Inspect the bounded output only if needed, continue the existing work, and communicate only when the existing request requires it.
