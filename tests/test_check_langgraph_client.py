import unittest

from scripts.check_langgraph_client import REASON, violations


class LangGraphClientCheckTests(unittest.TestCase):
    def test_reports_added_imports_aliases_and_calls_with_locations(self):
        diff = """diff --git a/openswe/example.py b/openswe/example.py
--- a/openswe/example.py
+++ b/openswe/example.py
@@ -1 +1,2 @@
-import os
+from openswe.utils.thread_ops import langgraph_client as legacy_client
+client = legacy_client()
@@ -20,0 +22,2 @@
+client = langgraph_client()
+client = get_langgraph_client()
"""
        self.assertEqual(
            violations(diff.splitlines()),
            [f"openswe/example.py:{line}: {REASON}" for line in (1, 22, 23)],
        )

    def test_allows_removal_and_unchanged_legacy_uses(self):
        diff = """diff --git a/openswe/example.py b/openswe/example.py
--- a/openswe/example.py
+++ b/openswe/example.py
@@ -1,3 +1,3 @@
 client = langgraph_client()
-client = get_langgraph_client()
+client = mda_client()
 value = client
"""
        self.assertEqual(violations(diff.splitlines()), [])

    def test_reports_new_files(self):
        diff = """diff --git a/openswe/new.py b/openswe/new.py
--- /dev/null
+++ b/openswe/new.py
@@ -0,0 +1 @@
+client = langgraph_client()
"""
        self.assertEqual(violations(diff.splitlines()), [f"openswe/new.py:1: {REASON}"])
