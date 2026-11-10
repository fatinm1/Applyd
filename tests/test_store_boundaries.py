from config import config
from parser import Job
from store import SQLiteJobStore

def test_rescan_preserves_decision_and_isolates_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAIL_SIGNING_SECRET", "offline-test-signing-secret")
    store = SQLiteJobStore(str(tmp_path / "jobs.db"))
    one = store.create_user("one", "password-one")
    two = store.create_user("two", "password-two")
    job = Job(id="same-job", company="Example", title="Engineer")
    store.save_job(job, owner_user_id=one)
    store.update_status(one, job.id, "approved")
    store.save_job(job, owner_user_id=one)
    assert store.get_job(one, job.id)["status"] == "approved"
    assert store.get_job(two, job.id) is None
    store.save_job(job, owner_user_id=two)
    assert store.get_job(two, job.id)["status"] == "pending"
    token, _ = store.issue_mail_action_token(one, job.id)
    assert store.verify_mail_action_token(job.id, token, owner_user_id=one)
    assert not store.verify_mail_action_token(job.id, token, owner_user_id=two)
    assert store.parse_and_verify_mail_action_token(token + "forged") is None
