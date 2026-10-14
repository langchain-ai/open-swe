"""Each incident thread's documents: its latest report, curated history, and postmortem."""

from openswe.incidents.document_models import IncidentHistory, IncidentPostmortem
from openswe.incidents.models import IncidentReportRecord
from openswe.threads.documents import ThreadDocuments

REPORTS = ThreadDocuments("incident_report", IncidentReportRecord)
HISTORY = ThreadDocuments("incident_history", IncidentHistory)
POSTMORTEMS = ThreadDocuments("incident_postmortem", IncidentPostmortem)
