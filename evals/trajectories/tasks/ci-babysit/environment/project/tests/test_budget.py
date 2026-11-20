from widgets.budget import RetryBudget


def test_budget_caps_retries_per_client():
    budget = RetryBudget(limit=2, window=60)
    assert budget.allow("a", now=0)
    assert budget.allow("a", now=1)
    assert not budget.allow("a", now=2)
    assert budget.allow("b", now=2)


def test_budget_resets_after_window():
    budget = RetryBudget(limit=1, window=10)
    assert budget.allow("a", now=0)
    assert budget.allow("a", now=11)
