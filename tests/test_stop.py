"""Stop must actually stop: the flag /api/stop sets has to survive freeing the run's quota slot."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from steltic import gate


def test_stop_flags_the_run_and_frees_its_slot_without_clearing_the_flag():
    key = "local:Ex1"
    gate.quota_acquire("local", key)
    assert not gate.is_cancelled(key)
    assert gate.stop(key) is True                # what /api/stop does
    assert gate.is_cancelled(key)                # the agent's cancel() lambda now says stop
    assert gate.quota_check("local") is None     # and the slot is free for the next run
    gate.quota_release(key)                      # the run's finally
    assert not gate.is_cancelled(key)


def test_the_old_sequence_lost_the_flag():
    """The bug: request_cancel() then quota_release() -- the second call discards the flag."""
    key = "local:Ex2"
    gate.quota_acquire("local", key)
    gate.request_cancel(key); gate.quota_release(key)
    assert not gate.is_cancelled(key)


def test_stopping_a_run_that_is_not_active_is_a_no_op():
    assert gate.stop("local:nothing") is False
    assert not gate.is_cancelled("local:nothing")


def test_a_new_run_of_the_same_project_starts_unflagged():
    key = "local:Ex3"
    gate.quota_acquire("local", key)
    gate.stop(key)
    gate.quota_acquire("local", key)             # the next Design building on the same project
    assert not gate.is_cancelled(key)
    gate.quota_release(key)
