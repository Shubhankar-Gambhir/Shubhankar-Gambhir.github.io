import subprocess
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "check_sketch.sh"

CLEAN = """
#include <atomic>
#include <thread>
int main() {
    std::atomic<int> c{0};
    std::thread a([&] { for (int i = 0; i < 10000; ++i) c.fetch_add(1); });
    std::thread b([&] { for (int i = 0; i < 10000; ++i) c.fetch_add(1); });
    a.join(); b.join();
    return c.load() == 20000 ? 0 : 1;
}
"""

RACY = """
#include <thread>
int main() {
    int c = 0;
    std::thread a([&] { for (int i = 0; i < 100000; ++i) ++c; });
    std::thread b([&] { for (int i = 0; i < 100000; ++i) ++c; });
    a.join(); b.join();
    return 0;
}
"""

WARN = """
int main() {
    int unused = 3;
    return 0;
}
"""


def check(tmp_path, src):
    f = tmp_path / "s.cpp"
    f.write_text(src)
    return subprocess.run([str(TOOL), str(f)], capture_output=True, text=True)


def test_clean_sketch_passes(tmp_path):
    r = check(tmp_path, CLEAN)
    assert r.returncode == 0, r.stdout + r.stderr


def test_racy_sketch_fails_tsan(tmp_path):
    r = check(tmp_path, RACY)
    assert r.returncode != 0
    assert "FAIL tsan" in r.stdout


def test_warning_fails(tmp_path):
    r = check(tmp_path, WARN)
    assert r.returncode != 0
    assert "FAIL werror" in r.stdout
