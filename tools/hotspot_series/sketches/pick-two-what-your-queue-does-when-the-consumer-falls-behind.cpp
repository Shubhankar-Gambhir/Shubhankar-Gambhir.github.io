// Sketch for "Pick Two: What Your Queue Does When the Consumer Falls Behind".
//
// A two-buffer logger whose overload policy is an explicit choice. Producers
// append to the active buffer under one mutex; the single consumer swaps it
// for an empty one and writes outside the lock. Losses are written into the
// output stream where they happened, and one slot is reserved so the shutdown
// message can never be dropped.
//
// Build: g++ -std=c++17 -O2 -Wall -Wextra -pthread <this file>
// The harness (below the class) runs 8 producers x 100k messages against a
// slow consumer for each policy and checks that every message is accounted
// for: delivered + dropped (as reported in-band) == produced.

#include <charconv>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

enum class OverloadPolicy { DropNewest, DropOldest, Block };

class TwoBufferLog {
    std::mutex m_;
    std::condition_variable ready_, room_;
    std::deque<std::optional<std::string>> active_, batch_;  // batch_: the consumer's buffer
    std::uint64_t dropped_ = 0;                       // losses since the last swap
    bool stop_ = false;
    const std::size_t slots_;
    const OverloadPolicy policy_;
    const std::function<void(std::string_view)> sink_;
public:
    TwoBufferLog(std::size_t slots, OverloadPolicy p, std::function<void(std::string_view)> sink)
        : slots_(slots < 2 ? 2 : slots), policy_(p), sink_(std::move(sink)) {}  // >= 1 usable
    bool push(std::string_view msg) {                 // any number of producers
        std::unique_lock lk(m_);
        if (policy_ == OverloadPolicy::Block)
            room_.wait(lk, [&] { return stop_ || active_.size() + 1 < slots_; });
        if (stop_) return false;
        if (active_.size() + 1 >= slots_) {           // full: the last slot is reserved
            ++dropped_;                               // counted at the point of loss
            if (policy_ == OverloadPolicy::DropNewest) return false;
            active_.pop_front();                      // DropOldest: evict, then append
        }
        active_.emplace_back(std::in_place, msg);
        ready_.notify_one();
        return true;
    }
    void shutdown() {                                 // a control message: never dropped
        std::lock_guard lk(m_);
        stop_ = true;
        active_.emplace_back(std::nullopt);           // always fits in the reserved slot
        ready_.notify_one();
        room_.notify_all();
    }
    void consumer_loop() {                            // exactly one consumer
        for (bool done = false; !done; batch_.clear()) {
            std::unique_lock lk(m_);
            ready_.wait(lk, [&] { return !active_.empty(); });  // a predicate, not a count
            batch_.swap(active_);
            std::uint64_t lost = std::exchange(dropped_, 0);
            lk.unlock();                              // write outside the lock
            room_.notify_all();
            std::string gap = "[" + std::to_string(lost) + " messages dropped]";
            if (lost && policy_ == OverloadPolicy::DropOldest) sink_(gap);  // lost before batch
            for (auto& m : batch_) { if (m) sink_(*m); else done = true; }  // nullopt: shutdown
            if (lost && policy_ == OverloadPolicy::DropNewest) sink_(gap);  // lost after batch
        }
    }
};

// ---------------------------------------------------------------------------
// Harness: not shown in the post.
// ---------------------------------------------------------------------------
namespace {

constexpr int kProducers = 8;

const char* name(OverloadPolicy p) {
    switch (p) {
    case OverloadPolicy::DropNewest: return "DropNewest";
    case OverloadPolicy::DropOldest: return "DropOldest";
    case OverloadPolicy::Block: return "Block";
    }
    return "?";
}

std::uint64_t parse_u64(std::string_view s) {
    std::uint64_t v = 0;
    std::from_chars(s.data(), s.data() + s.size(), v);
    return v;
}

// Runs one policy; returns true if every message is accounted for.
bool run(OverloadPolicy policy, std::size_t slots, int per_producer) {
    const std::uint64_t produced = std::uint64_t(kProducers) * per_producer;
    // Touched only by the consumer thread (inside the sink), read after join.
    std::uint64_t delivered = 0, dropped = 0, markers = 0;
    std::vector<long> last_seq(kProducers, -1);
    bool in_order = true;

    TwoBufferLog log(slots, policy, [&](std::string_view line) {
        if (line.front() == '[') {                        // "[N messages dropped]"
            dropped += parse_u64(line.substr(1));
            ++markers;
            return;
        }
        // "p<id> <seq>"
        std::size_t sp = line.find(' ');
        int id = int(parse_u64(line.substr(1, sp - 1)));
        long seq = long(parse_u64(line.substr(sp + 1)));
        if (seq <= last_seq[id]) in_order = false;        // per-producer order must hold
        last_seq[id] = seq;
        if (++delivered % 256 == 0)                       // a slow device
            std::this_thread::sleep_for(std::chrono::microseconds(100));
    });

    std::thread consumer([&] { log.consumer_loop(); });
    std::vector<std::thread> producers;
    for (int p = 0; p < kProducers; ++p) {
        producers.emplace_back([&log, p, per_producer] {
            std::string msg;
            for (int i = 0; i < per_producer; ++i) {
                msg = "p" + std::to_string(p) + " " + std::to_string(i);
                log.push(msg);
            }
        });
    }
    for (auto& t : producers) t.join();
    log.shutdown();
    consumer.join();

    bool ok = in_order && delivered + dropped == produced;
    if (policy == OverloadPolicy::Block) ok = ok && dropped == 0;
    std::printf("%-10s slots=%-3zu produced=%-7llu delivered=%-7llu dropped=%-7llu markers=%-6llu in_order=%d %s\n",
                name(policy), slots, (unsigned long long)produced, (unsigned long long)delivered, (unsigned long long)dropped,
                (unsigned long long)markers, int(in_order), ok ? "OK" : "FAIL");
    return ok;
}

}  // namespace

int main() {
    bool ok = true;
    for (auto p : {OverloadPolicy::DropNewest, OverloadPolicy::DropOldest, OverloadPolicy::Block})
        ok = run(p, 16, 100000) && ok;                    // 8 producers x 100k messages
    for (auto p : {OverloadPolicy::DropNewest, OverloadPolicy::DropOldest, OverloadPolicy::Block})
        ok = run(p, 2, 10000) && ok;                      // tiny buffer: one usable slot plus the reserved one
    return ok ? 0 : 1;
}
