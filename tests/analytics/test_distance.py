from agent.analytics.distance import _insert_delete_distance, _patch_lines


def test_patch_distance_ignores_context_and_diff_metadata():
    opening = _patch_lines(
        [
            {
                "filename": "agent/example.py",
                "changes": 2,
                "patch": "@@ -1,2 +1,2 @@\n-old = 1\n+new = 1\n context",
            }
        ]
    )
    final = _patch_lines(
        [
            {
                "filename": "agent/example.py",
                "changes": 2,
                "patch": "@@ -20,2 +20,2 @@\n-old = 1\n+new = 2\n context",
            }
        ]
    )

    assert opening is not None and final is not None
    assert _insert_delete_distance(opening, final) == 2


def test_patch_lines_rejects_incomplete_text_patch():
    assert _patch_lines([{"filename": "image.png", "changes": 1}]) is None
