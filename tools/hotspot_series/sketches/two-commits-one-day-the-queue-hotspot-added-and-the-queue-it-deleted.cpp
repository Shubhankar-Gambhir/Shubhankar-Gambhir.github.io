// Sketch for "Two Commits, One Day: The Queue HotSpot Added and the Queue It Deleted".
//
// OneSlot is the one-slot rendezvous HotSpot's VM thread has used since 2020.
// A requester installs a pointer to a request that lives on its own stack and
// waits until the server has run it. Capacity is one. The server clears the
// slot only after the work is done, so "the slot no longer holds my request"
// means "my request is finished". waited_behind() counts requests that found
// the slot taken, which is the traffic assumption ("rarely more than one in
// flight") made visible.
//
// FlagMailbox is a coalescing "there is work" mailbox in the style of JFR's
// PostBox: fetch_or to post, exchange(0) to drain, and a try_lock notify so a
// poster never blocks. A wake-up lost to a busy lock is bounded by the
// consumer's tick; a posted bit is never lost.
//
// Build: g++ -std=c++17 -O2 -Wall -Wextra -pthread <this file>
// The harness (below the classes) runs 16 requesters x 10k requests through
// OneSlot and checks that each request is served exactly once and that what
// the server wrote is visible to the requester. It then runs 4 posters x 2000
// acknowledged posts through FlagMailbox (no post may go unhandled), plus one
// burst of unacknowledged posts that must coalesce.

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

struct Request {                              // lives on the requester's stack
    std::function<void()> work;
};

class OneSlot {
    std::mutex m_;
    std::condition_variable server_cv_;       // the server waits here for a request
    std::condition_variable requester_cv_;    // requesters wait here: to install, then to finish
    Request* slot_ = nullptr;                 // capacity one
    bool stop_ = false;
    std::uint64_t waited_behind_ = 0;         // requests that found the slot taken
public:
    void submit_and_wait(Request& r) {        // any number of requesters
        std::unique_lock lk(m_);
        if (slot_ != nullptr) ++waited_behind_;
        requester_cv_.wait(lk, [&] { return slot_ == nullptr; });
        slot_ = &r;
        server_cv_.notify_one();
        requester_cv_.wait(lk, [&] { return slot_ != &r; });  // cleared only after the work
    }
    void serve_loop() {                       // exactly one server
        std::unique_lock lk(m_);
        for (;;) {
            server_cv_.wait(lk, [&] { return slot_ != nullptr || stop_; });
            if (slot_ == nullptr) return;     // stopped with nothing pending
            Request* r = slot_;
            lk.unlock();
            r->work();                        // outside the lock; r's owner is still waiting
            lk.lock();
            slot_ = nullptr;                  // "done" and "slot free" in one store
            requester_cv_.notify_all();
        }
    }
    void stop() {                             // call once every requester has returned
        std::lock_guard lk(m_);
        stop_ = true;
        server_cv_.notify_one();
    }
    std::uint64_t waited_behind() {
        std::lock_guard lk(m_);
        return waited_behind_;
    }
};

class FlagMailbox {                           // one bit per kind of "there is work"
    std::atomic<std::uint32_t> bits_{0};
    std::mutex m_;
    std::condition_variable cv_;
public:
    void post(std::uint32_t kinds) {          // never blocks
        bits_.fetch_or(kinds, std::memory_order_release);
        std::unique_lock lk(m_, std::try_to_lock);
        if (lk.owns_lock()) cv_.notify_all(); // a busy lock can cost a wake-up, never a bit
    }
    std::uint32_t collect(std::chrono::milliseconds tick) {  // one consumer
        std::unique_lock lk(m_);
        cv_.wait_for(lk, tick, [&] { return bits_.load(std::memory_order_acquire) != 0; });
        return bits_.exchange(0, std::memory_order_acquire);
    }
};

// ---------------------------------------------------------------------------
// Harness

static bool run_one_slot(int requesters, int per_requester) {
    OneSlot slot;
    const std::size_t total = static_cast<std::size_t>(requesters) * per_requester;
    std::vector<std::uint32_t> served(total, 0);  // written only by the server thread
    std::atomic<int> bad_results{0};

    std::thread server([&] { slot.serve_loop(); });
    std::vector<std::thread> threads;
    for (int t = 0; t < requesters; ++t) {
        threads.emplace_back([&, t] {
            for (int k = 0; k < per_requester; ++k) {
                const std::size_t id = static_cast<std::size_t>(t) * per_requester + k;
                std::size_t out = 0;          // on this thread's stack, written by the server
                Request r{[&served, &out, id] { ++served[id]; out = id + 1; }};
                slot.submit_and_wait(r);
                if (out != id + 1) bad_results.fetch_add(1, std::memory_order_relaxed);
            }
        });
    }
    for (auto& th : threads) th.join();
    slot.stop();
    server.join();

    std::size_t once = 0, other = 0;
    for (auto n : served) (n == 1 ? once : other) += 1;
    const bool ok = once == total && other == 0 && bad_results.load() == 0;
    std::printf("%s OneSlot: %d requesters x %d requests, served once %zu/%zu, bad results %d, "
                "waited behind another request %llu times\n",
                ok ? "OK  " : "FAIL", requesters, per_requester, once, total, bad_results.load(),
                static_cast<unsigned long long>(slot.waited_behind()));
    return ok;
}

static bool run_mailbox(int posters, int posts_each, int burst) {
    constexpr std::uint32_t kShutdown = 1u << 31;
    const int burst_bit = posters;            // bits 0..posters-1 belong to one poster each
    FlagMailbox box;
    std::vector<std::atomic<std::uint64_t>> handled(posters + 1);
    for (auto& h : handled) h.store(0);

    std::thread consumer([&] {
        for (bool done = false; !done;) {
            const std::uint32_t bits = box.collect(std::chrono::milliseconds(1));
            for (int b = 0; b <= posters; ++b)
                if (bits & (1u << b)) handled[b].fetch_add(1, std::memory_order_release);
            done = (bits & kShutdown) != 0;
        }
    });

    // Acknowledged posts: each poster owns one bit and waits for its post to be handled
    // before posting again, so every post must produce exactly one handling.
    std::vector<std::thread> threads;
    for (int p = 0; p < posters; ++p) {
        threads.emplace_back([&, p] {
            for (int k = 0; k < posts_each; ++k) {
                const std::uint64_t before = handled[p].load(std::memory_order_acquire);
                box.post(1u << p);
                while (handled[p].load(std::memory_order_acquire) == before)
                    std::this_thread::yield();
            }
        });
    }
    for (auto& th : threads) th.join();

    // Unacknowledged burst: posts of one kind merge, so handlings are between 1 and burst.
    for (int k = 0; k < burst; ++k) box.post(1u << burst_bit);
    box.post(kShutdown);
    consumer.join();

    bool ok = true;
    for (int p = 0; p < posters; ++p)
        ok = ok && handled[p].load() == static_cast<std::uint64_t>(posts_each);
    const std::uint64_t merged = handled[burst_bit].load();
    ok = ok && merged >= 1 && merged <= static_cast<std::uint64_t>(burst);
    std::printf("%s FlagMailbox: %d posters x %d acknowledged posts all handled; "
                "burst of %d posts handled %llu times\n",
                ok ? "OK  " : "FAIL", posters, posts_each, burst,
                static_cast<unsigned long long>(merged));
    return ok;
}

int main() {
    bool ok = run_one_slot(16, 10000);
    ok = run_mailbox(4, 2000, 1000) && ok;
    return ok ? 0 : 1;
}
