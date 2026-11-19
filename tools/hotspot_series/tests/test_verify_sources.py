import subprocess
import sys
from pathlib import Path

import pytest
import yaml

TOOL = Path(__file__).resolve().parent.parent / "verify_sources.py"


def run_tool(sidecar, tmp_path, repo=None):
    cmd = [sys.executable, str(TOOL), str(sidecar), "--cache", str(tmp_path / "cache"), "--delay", "0"]
    if repo:
        cmd += ["--repo", str(repo)]
    return subprocess.run(cmd, capture_output=True, text=True)


def write_sidecar(tmp_path, quotes=None, versions=None):
    p = tmp_path / "sidecar.yml"
    p.write_text(yaml.safe_dump({"post": "_drafts/x.md", "quotes": quotes or [], "versions": versions or []}))
    return p


def page(tmp_path, body):
    f = tmp_path / "page.html"
    f.write_text(body, encoding="utf-8")
    return f.as_uri()


def test_found_quote_passes(tmp_path):
    url = page(tmp_path, "<p>We cannot have all three guarantees at the same time.</p>")
    sc = write_sidecar(tmp_path, [{"id": "q1", "text": "cannot have all three guarantees", "url": url}])
    r = run_tool(sc, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FOUND q1" in r.stdout


def test_quote_not_at_url_fails(tmp_path):
    url = page(tmp_path, "<p>Something else entirely.</p>")
    sc = write_sidecar(tmp_path, [{"id": "q1", "text": "cannot have all three guarantees", "url": url}])
    r = run_tool(sc, tmp_path)
    assert r.returncode == 1
    assert "MISSING q1" in r.stdout


def test_normalization(tmp_path):
    url = page(tmp_path, "<p>it is <b>**non-blocking**</b>,\n  &quot;x&quot; and “y” it’s</p>")
    sc = write_sidecar(
        tmp_path,
        [
            {"id": "a", "text": 'non-blocking, "x"', "url": url},
            {"id": "b", "text": "\"y\" it's", "url": url},
        ],
    )
    r = run_tool(sc, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_jbs_browse_url_uses_rest():
    import verify_sources

    assert (
        verify_sources.rewrite_url("https://bugs.openjdk.org/browse/JDK-8391060")
        == "https://bugs.openjdk.org/rest/api/2/issue/JDK-8391060?fields=summary,description,comment"
    )
    assert verify_sources.rewrite_url("https://example.com/x") == "https://example.com/x"


def test_jbs_json_matches_description_and_comments():
    import verify_sources

    raw = (
        '{"fields": {"summary": "S", "description": "the <b>desc</b>",'
        ' "comment": {"comments": [{"body": "first comment"}, {"body": "second one"}]}}}'
    )
    text = verify_sources.extract_text("https://bugs.openjdk.org/rest/api/2/issue/JDK-1?x", raw)
    n = verify_sources.normalize(text)
    assert "the desc" in n and "second one" in n


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()

    def git(*a):
        return subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    shas = {}
    for name, tag in [("old", "jdk7-b24"), ("nine", "jdk-9+95"), ("new", "jdk-17+25")]:
        (r / "f.txt").write_text(name)
        git("add", "f.txt")
        git("commit", "-q", "-m", name)
        git("tag", tag)
        shas[name] = git("rev-parse", "--short=11", "HEAD")
    return r, shas


def test_tag_ok(tmp_path, repo):
    r, shas = repo
    sc = write_sidecar(tmp_path, versions=[{"id": "v", "commit": shas["new"], "expect_first_tag": "jdk-17+25"}])
    res = run_tool(sc, tmp_path, repo=r)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "TAG-OK v" in res.stdout


def test_tag_mismatch(tmp_path, repo):
    r, shas = repo
    sc = write_sidecar(tmp_path, versions=[{"id": "v", "commit": shas["new"], "expect_first_tag": "jdk-18+1"}])
    res = run_tool(sc, tmp_path, repo=r)
    assert res.returncode == 1
    assert "TAG-MISMATCH v" in res.stdout


def test_pre_jdk9_artifact_falls_back(tmp_path, repo):
    r, shas = repo
    sc = write_sidecar(tmp_path, versions=[{"id": "v", "commit": shas["old"], "expect_first_tag": "jdk7-b24"}])
    res = run_tool(sc, tmp_path, repo=r)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "TAG-OK v" in res.stdout
    # the artifact must not be accepted as the answer
    sc2 = write_sidecar(tmp_path, versions=[{"id": "v", "commit": shas["old"], "expect_first_tag": "jdk-9+95"}])
    assert run_tool(sc2, tmp_path, repo=r).returncode == 1
