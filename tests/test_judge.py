```python
import pytest
from unittest.mock import Mock, patch, MagicMock, PropertyMock, call
from uuid import UUID
from typing import Any

# Import the module under test - adjust the import path as needed
# For testing, we'll assume the module is named 'judge' and importable
# If not, we'll use mock patching at the module level.
import judge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_chat_anthropic():
    with patch('judge.ChatAnthropic') as mock:
        mock_instance = Mock()
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_judge_singleton(mock_chat_anthropic):
    # Ensure _judge is reset between tests
    judge._judge = None
    yield
    judge._judge = None


@pytest.fixture
def mock_run():
    run = Mock()
    run.inputs = {'question': 'What is Python?'}
    run.outputs = {
        'answer': 'A programming language',
        'confidence': 0.95,
        'citations': ['https://python.org']
    }
    return run


@pytest.fixture
def mock_example():
    example = Mock()
    example.inputs = {'question': 'What is Python?'}
    example.outputs = {
        'answer': 'Python is a high-level programming language',
        'severity': 'info',
        'file': 'test.py',
        'line': 10,
        'code': 'print("hello")',
        'explanation': 'Simple print'
    }
    return example


@pytest.fixture
def sample_candidate():
    return {
        'file': 'main.py',
        'line': 20,
        'code': 'import os',
        'explanation': 'Standard library import'
    }


@pytest.fixture
def sample_golden():
    return {
        'severity': 'warning',
        'file': 'main.py',
        'line': 20,
        'code': 'import os',
        'explanation': 'Should use pathlib instead'
    }


# ---------------------------------------------------------------------------
# Tests for _get_judge
# ---------------------------------------------------------------------------

class TestGetJudge:
    def test_returns_chat_anthropic_instance(self, mock_judge_singleton, mock_chat_anthropic):
        judge_instance = judge._get_judge()
        assert judge_instance is not None

    def test_singleton_behavior(self, mock_judge_singleton, mock_chat_anthropic):
        first = judge._get_judge()
        second = judge._get_judge()
        assert first is second

    def test_creates_new_judge_when_none(self, mock_judge_singleton):
        judge._judge = None
        assert judge._get_judge() is not None

    def test_returns_existing_judge(self, mock_judge_singleton):
        mock_instance = Mock()
        judge._judge = mock_instance
        assert judge._get_judge() is mock_instance

    def test_no_exception_on_multiple_calls(self, mock_judge_singleton, mock_chat_anthropic):
        for _ in range(10):
            judge._get_judge()
        # Should not raise


# ---------------------------------------------------------------------------
# Tests for _format_candidate
# ---------------------------------------------------------------------------

class TestFormatCandidate:
    def test_full_candidate(self, sample_candidate):
        result = judge._format_candidate(sample_candidate)
        assert 'File: main.py' in result
        assert 'Line: 20' in result
        assert 'Code: import os' in result
        assert 'Explanation: Standard library import' in result

    def test_missing_file_key(self):
        candidate = {'line': 5, 'code': 'x = 1', 'explanation': 'Assign'}
        result = judge._format_candidate(candidate)
        # Should still output other fields without file
        assert 'File: N/A' in result or 'File:' not in result  # depends on implementation
        # We expect missing keys to be replaced with fallback
        assert 'Line: 5' in result

    def test_empty_candidate(self):
        result = judge._format_candidate({})
        assert 'Explanation: N/A' in result or result == ''

    def test_none_candidate(self):
        with pytest.raises(TypeError):
            judge._format_candidate(None)

    def test_candidate_with_extra_fields(self, sample_candidate):
        sample_candidate['extra'] = 'ignore'
        result = judge._format_candidate(sample_candidate)
        assert 'extra' not in result

    def test_numeric_line(self):
        candidate = {'file': 'a.py', 'line': 42, 'code': 'pass', 'explanation': 'placeholder'}
        result = judge._format_candidate(candidate)
        assert 'Line: 42' in result


# ---------------------------------------------------------------------------
# Tests for _format_golden
# ---------------------------------------------------------------------------

class TestFormatGolden:
    def test_full_golden(self, sample_golden):
        result = judge._format_golden(sample_golden)
        assert 'Severity: warning' in result
        assert 'File: main.py' in result

    def test_missing_severity(self):
        golden = {'file': 'test.py', 'line': 1, 'code': 'x', 'explanation': 'y'}
        result = judge._format_golden(golden)
        # Should still format without severity
        assert 'Severity: N/A' in result or 'Severity:' not in result

    def test_empty_golden(self):
        result = judge._format_golden({})
        assert 'Severity: N/A' in result or result == ''

    def test_none_golden(self):
        with pytest.raises(TypeError):
            judge._format_golden(None)

    def test_integer_severity(self):
        golden = {'severity': 5, 'file': 'x.py'}
        result = judge._format_golden(golden)
        assert 'Severity: 5' in result


# ---------------------------------------------------------------------------
# Tests for _judge_pair
# ---------------------------------------------------------------------------

class TestJudgePair:
    @patch('judge._get_judge')
    @patch('judge._format_candidate')
    @patch('judge._format_golden')
    def test_basic_judgment(self, mock_fmt_golden, mock_fmt_cand, mock_get_judge):
        mock_judge = Mock()
        mock_get_judge.return_value = mock_judge

        # Mock response from Anthropic
        mock_judge.invoke.return_value.content = """
        classification: MATCH
        precision_score: 0.8
        recall_score: 0.9
        f1_score: 0.85
        judge_comment: Good match
        """

        mock_fmt_golden.return_value = "Golden formatted"
        mock_fmt_cand.return_value = "Candidate formatted"

        golden = {'severity': 'info'}
        candidate = {'file': 'test.py'}

        result = judge._judge_pair(golden, candidate)

        # Check prompt was built correctly
        mock_judge.invoke.assert_called_once()
        call_args = mock_judge.invoke.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0]['role'] == 'system'
        assert call_args[1]['role'] == 'user'
        assert 'Golden formatted' in call_args[1]['content']
        assert 'Candidate formatted' in call_args[1]['content']

        # Verify parsed output
        assert result['classification'] == 'MATCH'
        assert result['precision_score'] == 0.8
        assert result['recall_score'] == 0.9
        assert result['f1_score'] == 0.85
        assert result['judge_comment'] == 'Good match'

    @patch('judge._get_judge')
    def test_missing_fields_in_response(self, mock_get_judge):
        mock_judge = Mock()
        mock_get_judge.return_value = mock_judge
        mock_judge.invoke.return_value.content = """
        classification: NON_MATCH
        """

        result = judge._judge_pair({}, {})
        assert result['classification'] == 'NON_MATCH'
        # other fields should be None or default
        assert result.get('precision_score') is None

    @patch('judge._get_judge')
    def test_invalid_response_format(self, mock_get_judge):
        mock_judge = Mock()
        mock_get_judge.return_value = mock_judge
        mock_judge.invoke.return_value.content = "Not a valid response"

        result = judge._judge_pair({}, {})
        # Should handle gracefully, maybe return default dict
        assert isinstance(result, dict)

    @patch('judge._get_judge')
    def test_non_numeric_scores(self, mock_get_judge):
        mock_judge = Mock()
        mock_get_judge.return_value = mock_judge
        mock_judge.invoke.return_value.content = """
        classification: MATCH
        precision_score: abc
        recall_score: 123
        f1_score: 0.5
        """

        result = judge._judge_pair({}, {})
        # precision_score should be None due to conversion error
        assert result['precision_score'] is None
        assert result['recall_score'] == 123
        assert result['f1_score'] == 0.5


# ---------------------------------------------------------------------------
# Tests for _record_counts and _drain_counts
# ---------------------------------------------------------------------------

class TestRecordAndDrainCounts:
    def setup_method(self):
        # Reset counts before each test
        judge._COUNTS = []
        judge._COUNTS_LOCK = Mock()

    @patch('builtins.open', new_callable=MagicMock)
    def test_record_counts_calls_lock_and_appends(self, mock_open):
        test_id = UUID('12345678-1234-5678-1234-567812345678')
        counts = {'precision': 0.9, 'recall': 0.8}
        judge._record_counts(test_id, counts)
        judge._COUNTS_LOCK.__enter__.assert_called_once()
        assert len(judge._COUNTS) == 1
        assert judge._COUNTS[0]['example_id'] == test_id
        assert judge._COUNTS[0]['precision'] == 0.9

    def test_drain_counts_returns_list_and_clears(self):
        judge._COUNTS = [{'a': 1}, {'b': 2}]
        result = judge._drain_counts()
        assert result == [{'a': 1}, {'b': 2}]
        assert judge._COUNTS == []

    def test_record_counts_appends_multiple(self):
        id1 = UUID('11111111-1111-1111-1111-111111111111')
        id2 = UUID('22222222-2222-2222-2222-222222222222')
        judge._record_counts(id1, {'x': 1})
        judge._record_counts(id2, {'y': 2})
        assert len(judge._COUNTS) == 2


# ---------------------------------------------------------------------------
# Tests for judge_match
# ---------------------------------------------------------------------------

class TestJudgeMatch:
    @patch('judge._judge_pair')
    @patch('judge._format_candidate')
    @patch('judge._format_golden')
    @patch('judge._record_counts')
    def test_judge_match_flow(self, mock_record, mock_fmt_golden, mock_fmt_cand, mock_judge_pair):
        run = Mock()
        run.inputs = {'question': 'What is Python?'}
        run.outputs = {'answer': 'A language', 'file': 'a.py', 'line': 1, 'code': 'print', 'explanation': 'test'}

        example = Mock()
        example.inputs = {'question': 'What is Python?'}
        example.outputs = {'answer': 'A language', 'severity': 'info', 'file': 'a.py', 'line': 1, 'code': 'print', 'explanation': 'test'}

        mock_fmt_cand.return_value = "Candidate"
        mock_fmt_golden.return_value = "Golden"
        mock_judge_pair.return_value = {'classification': 'MATCH', 'precision_score': 1.0, 'recall_score': 1.0, 'f1_score': 1.0, 'judge_comment': 'ok'}

        result = judge.judge_match(run, example)

        mock_fmt_cand.assert_called_once_with(run.outputs)
        mock_fmt_golden.assert_called_once_with(example.outputs)
        mock_judge_pair.assert_called_once()
        mock_record.assert_called_once()

        # Verify result keys
        assert 'classification' in result
        assert 'example_id' in result
        assert 'run_id' in result
        assert result['classification'] == 'MATCH'

    @patch('judge._judge_pair')
    @patch('judge._format_candidate')
    @patch('judge._format_golden')
    @patch('judge._record_counts')
    def test_judge_match_none_inputs(self, mock_record, mock_fmt_golden, mock_fmt_cand, mock_judge_pair):
        run = Mock()
        run.inputs = None
        run.outputs = {}
        example = Mock()
        example.inputs = None
        example.outputs = {}
        mock_fmt_cand.return_value = ""
        mock_fmt_golden.return_value = ""
        mock_judge_pair.return_value = {}

        result = judge.judge_match(run, example)
        assert isinstance(result, dict)

    def test_judge_match_missing_run_id(self):
        run = Mock(spec=[])  # No 'id' attribute
        example = Mock()
        with pytest.raises(AttributeError):
            judge.judge_match(run, example)


# ---------------------------------------------------------------------------
# Tests for _f1
# ---------------------------------------------------------------------------

class TestF1:
    def test_perfect_scores(self):
        assert judge._f1(1.0, 1.0) == 1.0

    def test_zero_precision(self):
        assert judge._f1(0.0, 1.0) == 0.0

    def test_zero_recall(self):
        assert judge._f1(1.0, 0.0) == 0.0

    def test_both_zero(self):
        assert judge._f1(0.0, 0.0) == 0.0

    def test_typical_values(self):
        assert judge._f1(0.8, 0.9) == pytest.approx(2*0.8*0.9/(0.8+0.9), rel=1e-9)

    def test_half_values(self):
        assert judge._f1(0.5, 0.5) == 0.5

    def test_high_precision_low_recall(self):
        f1 = judge._f1(0.95, 0.1)
        expected = 2*0.95*0.1/(0.95+0.1)
        assert f1 == pytest.approx(expected)

    def test_float_inputs(self):
        assert judge._f1(0.3, 0.7) == pytest.approx(2*0.3*0.7/(0.3+0.7))


# ---------------------------------------------------------------------------
# Tests for aggregate_pr
# ---------------------------------------------------------------------------

class TestAggregatePR:
    @patch('judge._drain_counts')
    def test_aggregate_with_empty_lists(self, mock_drain):
        mock_drain.return_value = []
        result = judge.aggregate_pr([], [])
        assert result['total_precision'] == 0
        assert result['total_recall'] == 0
        assert result['average_f1'] == 0
        assert result['num_examples'] == 0

    @patch('judge._drain_counts')
    def test_aggregate_with_counts(self, mock_drain):
        mock_drain.return_value = [
            {'precision': 0.8, 'recall': 0.9},
            {'precision': 0.7, 'recall': 0