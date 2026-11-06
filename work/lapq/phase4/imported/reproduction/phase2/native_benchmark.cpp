// Independent native crosscheck, not an exact translation of the paper artifact.
// C++17; no external libraries. Queue ablations use one scalar-key/LIFO-vertex
// Dijkstra kernel; direct_heap preserves these ties without its pending map.
// Plain and guided skiplists group equal live keys and reclaim
// deleted nodes.  Guided variants differ ONLY in their rank predecessor index.
// The hierarchical bitmap is O(log_64 U), not a van Emde Boas implementation.
// Build: clang++ -std=c++17 -O3 -DNDEBUG native_benchmark.cpp -o native_benchmark
// --selftest runs correctness checks; graph mode writes PREFIX.csv/PREFIX.json.

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <optional>
#include <queue>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using Rank = uint32_t;
using Clock = std::chrono::steady_clock;
constexpr double INF = std::numeric_limits<double>::infinity();
static void require(bool x, const std::string& message) {
    if (!x) throw std::runtime_error(message);
}
// Literal checks in the timed kernel must not allocate temporary std::strings.
static void require(bool x, const char* message) {
    if (!x) throw std::runtime_error(message);
}
static unsigned highest_bit(uint64_t x) {
    require(x != 0, "bit scan of zero");
    return 63U - static_cast<unsigned>(__builtin_clzll(x));
}
static uint64_t inclusive_mask(unsigned bit) {
    return bit == 63 ? ~uint64_t(0) : ((uint64_t(1) << (bit + 1)) - 1);
}
static std::string quote(const std::string& s) {
    std::ostringstream o; o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') o << '\\' << c;
        else if (c == '\n') o << "\\n";
        else if (c == '\r') o << "\\r";
        else if (c == '\t') o << "\\t";
        else if (c < 32) o << "\\u" << std::hex << std::setw(4)
                           << std::setfill('0') << unsigned(c) << std::dec;
        else o << c;
    }
    return o.str() + '"';
}
struct Stopwatch {
    Clock::time_point wall = Clock::now();
    std::clock_t cpu = std::clock();
    std::pair<double, double> seconds() const {
        return {std::chrono::duration<double>(Clock::now() - wall).count(),
                double(std::clock() - cpu) / CLOCKS_PER_SEC};
    }
};

// Dense 64-ary occupancy hierarchy. Parent bits summarize nonempty child words.
class BitmapIndex {
    uint64_t universe_;
    size_t size_ = 0;
    std::vector<std::vector<uint64_t>> levels_;
    std::optional<uint64_t> at_most(size_t level, uint64_t x) const {
        const uint64_t word = x >> 6;
        const unsigned bit = unsigned(x & 63);
        uint64_t bits = levels_[level][size_t(word)] & inclusive_mask(bit);
        if (bits) return word * 64 + highest_bit(bits);
        if (word == 0 || level + 1 == levels_.size()) return std::nullopt;
        const auto previous = at_most(level + 1, word - 1);
        if (!previous) return std::nullopt;
        bits = levels_[level][size_t(*previous)];
        require(bits != 0, "bitmap parent points to empty word");
        return *previous * 64 + highest_bit(bits);
    }
public:
    explicit BitmapIndex(uint64_t universe) : universe_(universe) {
        require(universe > 0 && universe <= (uint64_t(1) << 32), "invalid rank universe");
        uint64_t count = (universe + 63) / 64;
        do {
            levels_.emplace_back(size_t(count), 0);
            if (count == 1) break;
            count = (count + 63) / 64;
        } while (true);
    }
    bool add(Rank rank) {
        require(uint64_t(rank) < universe_, "rank outside bitmap universe");
        uint64_t x = rank;
        for (size_t level = 0; level < levels_.size(); ++level) {
            auto& word = levels_[level][size_t(x >> 6)];
            const uint64_t mask = uint64_t(1) << (x & 63);
            if (level == 0 && (word & mask)) return false;
            const bool was_empty = word == 0;
            word |= mask;
            if (!was_empty) break;
            x >>= 6;
        }
        ++size_; return true;
    }
    bool discard(Rank rank) {
        if (uint64_t(rank) >= universe_) return false;
        uint64_t x = rank;
        for (size_t level = 0; level < levels_.size(); ++level) {
            auto& word = levels_[level][size_t(x >> 6)];
            const uint64_t mask = uint64_t(1) << (x & 63);
            if (level == 0 && !(word & mask)) return false;
            word &= ~mask;
            if (word != 0) break;
            x >>= 6;
        }
        --size_; return true;
    }
    std::optional<Rank> predecessor_strict(uint64_t rank) const {
        if (rank == 0 || size_ == 0) return std::nullopt;
        auto p = at_most(0, std::min(rank - 1, universe_ - 1));
        return p ? std::optional<Rank>(Rank(*p)) : std::nullopt;
    }
    size_t size() const { return size_; }
    size_t backing_bytes() const {
        size_t n = 0; for (const auto& v : levels_) n += v.capacity() * sizeof(uint64_t);
        return n; // word storage only, not containers, allocator overhead or RSS
    }
};
class SetIndex {
    std::set<Rank> values_;
public:
    explicit SetIndex(uint64_t) {}
    bool add(Rank r) { return values_.insert(r).second; }
    bool discard(Rank r) { return values_.erase(r) != 0; }
    std::optional<Rank> predecessor_strict(uint64_t r) const {
        auto it = r > std::numeric_limits<Rank>::max() ? values_.end()
                                                    : values_.lower_bound(Rank(r));
        if (it == values_.begin()) return std::nullopt;
        return *--it;
    }
    size_t size() const { return values_.size(); }
    size_t backing_bytes() const { return 0; } // allocation layout is not measured
};

struct HeapQueue {
    std::priority_queue<double, std::vector<double>, std::greater<double>> q;
    HeapQueue(uint64_t, uint64_t) {}
    void push(double key, Rank) { q.push(key); }
    double pop() { double x = q.top(); q.pop(); return x; }
    bool empty() const { return q.empty(); }
    size_t live_entries() const { return q.size(); }
    size_t live_nodes() const { return q.size(); }
};
struct MultisetQueue {
    std::multiset<double> q;
    MultisetQueue(uint64_t, uint64_t) {}
    void push(double key, Rank) { q.insert(key); }
    double pop() { auto it = q.begin(); double x = *it; q.erase(it); return x; }
    bool empty() const { return q.empty(); }
    size_t live_entries() const { return q.size(); }
    size_t live_nodes() const { return q.size(); }
};
struct GroupedHeapQueue {
    std::priority_queue<double, std::vector<double>, std::greater<double>> q;
    std::unordered_map<double, size_t> multiplicities;
    size_t live = 0;
    GroupedHeapQueue(uint64_t, uint64_t) {}
    void push(double key, Rank) {
        auto result = multiplicities.emplace(key, 0);
        if (result.second) q.push(key);
        ++result.first->second; ++live;
    }
    double pop() {
        const double x = q.top(); auto it = multiplicities.find(x);
        if (--it->second == 0) { multiplicities.erase(it); q.pop(); }
        --live; return x;
    }
    bool empty() const { return live == 0; }
    size_t live_entries() const { return live; }
    size_t live_nodes() const { return multiplicities.size(); }
};

// The fixed-height head is a sentinel. Ordinary nodes allocate only their height.
// Intrusive bucket links retain only live nodes and permit O(1) collision removal.
template<class Index, bool Guided>
class SkipQueue {
    struct Node {
        double key;
        Rank rank;
        size_t multiplicity = 1;
        unsigned height;
        std::vector<Node*> next, prev;
        Node *bucket_prev = nullptr, *bucket_next = nullptr;
        Node(double k, Rank r, unsigned h) : key(k), rank(r), height(h),
                                             next(h, nullptr), prev(h, nullptr) {}
    };
    struct Bucket { Node *first = nullptr, *last = nullptr; };
    Node head_{-INF, 0, 64};
    unsigned top_ = 1;
    size_t live_ = 0;
    std::mt19937_64 rng_;
    Index index_;
    std::unordered_map<double, Node*> nodes_;
    std::unordered_map<Rank, Bucket> buckets_;
    unsigned height(const Node* node) const { return node == &head_ ? top_ : node->height; }
    unsigned draw_height() {
        const uint64_t x = rng_();
        return x == 0 ? 64 : std::min(64U, 1U + unsigned(__builtin_ctzll(x)));
    }
    Node* ordinary_predecessor(double key) {
        Node* current = &head_;
        for (int h = int(top_) - 1; h >= 0; --h)
            while (current->next[size_t(h)] && current->next[size_t(h)]->key <= key)
                current = current->next[size_t(h)];
        return current;
    }
    Node* finger_predecessor(Node* current, double key) {
        if (current->next[0] && current->next[0]->key < key) {
            while (current->next[height(current) - 1] &&
                   current->next[height(current) - 1]->key <= key)
                current = current->next[height(current) - 1];
            for (int h = int(height(current)) - 1; h >= 0; --h)
                while (current->next[size_t(h)] && current->next[size_t(h)]->key <= key)
                    current = current->next[size_t(h)];
            return current;
        }
        if (current->key > key) {
            while (current->prev[height(current) - 1]->key >= key)
                current = current->prev[height(current) - 1];
            for (int h = int(height(current)) - 1; h >= 0; --h)
                while (current->prev[size_t(h)]->key >= key)
                    current = current->prev[size_t(h)];
            return current->prev[0];
        }
        return current;
    }
    void append_bucket(Node* node) {
        auto result = buckets_.emplace(node->rank, Bucket{});
        Bucket& bucket = result.first->second;
        if (result.second) index_.add(node->rank);
        node->bucket_prev = bucket.last; node->bucket_next = nullptr;
        if (bucket.last) bucket.last->bucket_next = node;
        else bucket.first = node;
        bucket.last = node;
    }
    void unlink_bucket(Node* node, bool erase_empty) {
        auto it = buckets_.find(node->rank); Bucket& bucket = it->second;
        if (node->bucket_prev) node->bucket_prev->bucket_next = node->bucket_next;
        else bucket.first = node->bucket_next;
        if (node->bucket_next) node->bucket_next->bucket_prev = node->bucket_prev;
        else bucket.last = node->bucket_prev;
        node->bucket_prev = node->bucket_next = nullptr;
        if (erase_empty && !bucket.first) {
            index_.discard(node->rank); buckets_.erase(it);
        }
    }
public:
    SkipQueue(uint64_t seed, uint64_t universe) : rng_(seed), index_(universe) {}
    SkipQueue(const SkipQueue&) = delete;
    SkipQueue& operator=(const SkipQueue&) = delete;
    ~SkipQueue() {
        Node* node = head_.next[0];
        while (node) { Node* next = node->next[0]; delete node; node = next; }
    }
    void push(double key, Rank rank) {
        auto found = nodes_.find(key);
        if (found != nodes_.end()) {
            Node* node = found->second;
            if constexpr (Guided) {
                require(node->rank == rank, "live equal key received changing rank");
                unlink_bucket(node, false); append_bucket(node);
            }
            ++node->multiplicity; ++live_; return;
        }
        Node* predecessor;
        if constexpr (Guided) {
            Node* hint = &head_;
            const auto previous_rank = index_.predecessor_strict(rank);
            if (previous_rank) hint = buckets_.at(*previous_rank).last;
            predecessor = finger_predecessor(hint, key);
        } else predecessor = ordinary_predecessor(key);
        auto node_owner = std::make_unique<Node>(key, rank, draw_height());
        Node* node = node_owner.get(); top_ = std::max(top_, node->height);
        for (unsigned h = 0; h < node->height; ++h) {
            while (h >= height(predecessor)) predecessor = predecessor->prev[h - 1];
            Node* successor = predecessor->next[h];
            node->prev[h] = predecessor; node->next[h] = successor;
            predecessor->next[h] = node;
            if (successor) successor->prev[h] = node;
        }
        nodes_.emplace(key, node);
        if constexpr (Guided) append_bucket(node);
        node_owner.release(); ++live_;
    }
    double pop() {
        Node* node = head_.next[0]; require(node != nullptr, "empty skiplist extraction");
        const double key = node->key; --live_;
        if (--node->multiplicity) return key;
        for (unsigned h = 0; h < node->height; ++h) {
            node->prev[h]->next[h] = node->next[h];
            if (node->next[h]) node->next[h]->prev[h] = node->prev[h];
        }
        if constexpr (Guided) unlink_bucket(node, true);
        nodes_.erase(key); delete node; return key;
    }
    bool empty() const { return live_ == 0; }
    size_t live_entries() const { return live_; }
    size_t live_nodes() const { return nodes_.size(); }
    void validate() const {
        size_t entries = 0, unique = 0; const Node* previous = &head_;
        std::set<Rank> ranks;
        for (const Node* n = head_.next[0]; n; n = n->next[0]) {
            require(previous->key < n->key && n->prev[0] == previous, "skiplist order/link");
            require(nodes_.at(n->key) == n && n->multiplicity > 0, "live node map");
            for (unsigned h = 0; h < n->height; ++h) {
                require(n->prev[h]->next[h] == n, "skiplist previous reciprocity");
                if (n->next[h]) require(n->next[h]->prev[h] == n && n->next[h]->key > n->key,
                                        "skiplist next reciprocity");
            }
            entries += n->multiplicity; ++unique; ranks.insert(n->rank); previous = n;
        }
        require(entries == live_ && unique == nodes_.size(), "skiplist live accounting");
        if constexpr (Guided) {
            require(ranks.size() == index_.size() && ranks.size() == buckets_.size(), "rank accounting");
            size_t bucket_nodes = 0;
            for (const auto& item : buckets_) {
                const Node* prev = nullptr;
                for (const Node* n = item.second.first; n; n = n->bucket_next) {
                    require(n->rank == item.first && n->bucket_prev == prev, "rank bucket links");
                    require(nodes_.at(n->key) == n, "stale bucket node"); prev = n; ++bucket_nodes;
                }
                require(prev == item.second.last && prev != nullptr, "rank bucket tail");
            }
            require(bucket_nodes == nodes_.size(), "rank bucket node count");
        }
    }
};

class RadixQueue {
    std::array<std::vector<uint64_t>, 65> buckets_;
    uint64_t last_ = 0; size_t live_ = 0;
    static unsigned bucket(uint64_t x, uint64_t last) {
        const uint64_t v = x ^ last; return v == 0 ? 0 : highest_bit(v) + 1;
    }
public:
    RadixQueue(uint64_t, uint64_t) {}
    void push(double key, Rank) {
        require(key >= 0 && key <= 9007199254740991.0 && std::floor(key) == key,
                "radix requires exact nonnegative integer key below 2^53");
        const auto k = uint64_t(key); require(k >= last_, "nonmonotone radix insertion");
        buckets_[bucket(k, last_)].push_back(k); ++live_;
    }
    double pop() {
        require(live_ > 0, "empty radix extraction");
        if (buckets_[0].empty()) {
            unsigned i = 1; while (buckets_[i].empty()) ++i;
            last_ = *std::min_element(buckets_[i].begin(), buckets_[i].end());
            for (uint64_t key : buckets_[i]) buckets_[bucket(key, last_)].push_back(key);
            buckets_[i].clear();
        }
        const uint64_t key = buckets_[0].back(); buckets_[0].pop_back(); --live_;
        return double(key);
    }
    bool empty() const { return live_ == 0; }
    size_t live_entries() const { return live_; }
    size_t live_nodes() const { return live_; }
};

struct Edge { uint32_t to; double weight; };
struct Graph {
    std::vector<std::vector<Edge>> adj;
    uint64_t edges = 0;
    double max_weight = 0;
    bool integral = true;
};
static Graph read_graph(const std::string& path) {
    std::ifstream f(path); require(bool(f), "cannot open graph: " + path);
    uint64_t n, m; require(bool(f >> n >> m), "invalid graph header");
    require(n > 0 && n <= std::numeric_limits<uint32_t>::max(), "invalid vertex count");
    Graph g; g.adj.resize(size_t(n)); g.edges = m;
    for (uint64_t i = 0; i < m; ++i) {
        uint64_t u, v; double w;
        require(bool(f >> u >> v >> w), "missing or invalid graph edge");
        require(u < n && v < n && std::isfinite(w) && w >= 0, "invalid graph edge value");
        g.adj[size_t(u)].push_back({uint32_t(v), w});
        g.integral = g.integral && std::floor(w) == w;
        g.max_weight = std::max(g.max_weight, w);
    }
    std::string extra; require(!(f >> extra), "unexpected data after graph edges");
    return g;
}
static std::vector<double> oracle(const Graph& g, uint32_t source) {
    using Item = std::pair<double, uint32_t>;
    std::priority_queue<Item, std::vector<Item>, std::greater<Item>> q;
    std::vector<double> d(g.adj.size(), INF); d[source] = 0; q.push({0, source});
    while (!q.empty()) {
        auto item = q.top(); q.pop(); if (item.first != d[item.second]) continue;
        for (const auto& edge : g.adj[item.second]) {
            const double next = item.first + edge.weight;
            require(std::isfinite(next), "distance overflow");
            if (next < d[edge.to]) { d[edge.to] = next; q.push({next, edge.to}); }
        }
    }
    return d;
}
static std::vector<double> bellman_ford(const Graph& g, uint32_t source) {
    std::vector<double> d(g.adj.size(), INF); d[source] = 0;
    for (size_t k = 1; k < g.adj.size(); ++k) {
        bool changed = false;
        for (size_t u = 0; u < g.adj.size(); ++u) if (std::isfinite(d[u]))
            for (const auto& edge : g.adj[u]) if (d[u] + edge.weight < d[edge.to]) {
                d[edge.to] = d[u] + edge.weight; changed = true;
            }
        if (!changed) break;
    }
    return d;
}
struct NoPrediction { Rank operator()(double) const { return 0; } };
struct ExactPrediction {
    std::vector<double> values;
    explicit ExactPrediction(const std::vector<double>& samples) : values(samples) {
        std::sort(values.begin(), values.end());
    }
    Rank operator()(double x) const {
        return Rank(std::lower_bound(values.begin(), values.end(), x) - values.begin());
    }
};
struct LinearPrediction {
    double low = 0, high = 0; Rank count = 0;
    explicit LinearPrediction(const std::vector<double>& samples) : count(Rank(samples.size())) {
        if (!samples.empty()) { auto p = std::minmax_element(samples.begin(), samples.end());
            low = *p.first; high = *p.second; }
    }
    Rank operator()(double x) const {
        if (high == low || x <= low) return 0;
        if (x >= high) return count;
        const double estimate = (x - low) / (high - low) * double(count);
        return Rank(std::min(double(count), std::max(0.0, estimate)));
    }
};
struct HistogramPrediction {
    static constexpr size_t B = 64;
    double low = 0, high = 0, width = 0; Rank count = 0;
    std::array<uint64_t, B + 1> prefix{};
    explicit HistogramPrediction(const std::vector<double>& samples) : count(Rank(samples.size())) {
        if (samples.empty()) return;
        std::array<uint64_t, B> bins{};
        const auto p = std::minmax_element(samples.begin(), samples.end());
        low = *p.first; high = *p.second; width = high - low;
        if (width > 0) for (double x : samples) {
            const size_t bin = std::min(B - 1, size_t(std::max(0.0, (x - low) * double(B) / width)));
            ++bins[bin];
        }
        else bins[0] = samples.size();
        for (size_t i = 0; i < B; ++i) prefix[i + 1] = prefix[i] + bins[i];
    }
    Rank operator()(double x) const {
        if (width == 0 || x <= low) return 0;
        if (x >= high) return count;
        const double position = (x - low) / width * double(B);
        const size_t bin = std::min(B - 1, size_t(std::max(0.0, position)));
        const double fraction = std::min(1.0, std::max(0.0, position - double(bin)));
        return Rank(std::min(double(count), double(prefix[bin]) +
                        fraction * double(prefix[bin + 1] - prefix[bin])));
    }
};
struct RunResult {
    std::vector<double> distances;
    uint64_t insertions = 0, extractions = 0, peak_entries = 0, peak_nodes = 0;
    uint64_t final_entries = 0, final_pending = 0;
};
// Deployment control: embed vertices in heap entries and avoid the pending map.
// Descending insertion sequence within equal keys gives exactly the same LIFO
// vertex rule as the common scalar-key kernel, including stale duplicates.
template<bool Observe, bool Collect>
static RunResult execute_direct_heap(const Graph& g, uint32_t source,
                                     std::vector<double>* samples = nullptr) {
    struct Entry { double key; uint64_t sequence; uint32_t vertex; };
    struct Later {
        bool operator()(const Entry& a, const Entry& b) const {
            if (a.key != b.key) return a.key > b.key;
            return a.sequence < b.sequence;
        }
    };
    std::priority_queue<Entry, std::vector<Entry>, Later> q;
    RunResult result; result.distances.assign(g.adj.size(), INF);
    uint64_t next_sequence = 0;
    auto add = [&](double key, uint32_t vertex, bool initial) {
        q.push({key, next_sequence++, vertex});
        if constexpr (Collect) { if (!initial) samples->push_back(key); }
        if constexpr (Observe) {
            ++result.insertions;
            result.peak_entries = std::max(result.peak_entries, uint64_t(q.size()));
            result.peak_nodes = result.peak_entries;
        }
    };
    result.distances[source] = 0; add(0, source, true);
    while (!q.empty()) {
        const Entry entry = q.top(); q.pop();
        if constexpr (Observe) ++result.extractions;
        if (entry.key > result.distances[entry.vertex]) continue;
        for (const Edge& edge : g.adj[entry.vertex]) {
            const double next = entry.key + edge.weight;
            require(std::isfinite(next), "distance overflow");
            if (next < result.distances[edge.to]) {
                result.distances[edge.to] = next; add(next, edge.to, false);
            }
        }
    }
    result.final_entries = q.size(); return result;
}
// Observe=false compiles out diagnostic counters. Collect=true records training
// keys or untimed selftest traces; it is never enabled in query timing.
// Allocations, queue construction/destruction and distance allocation are timed.
template<bool Observe, bool Collect, class Queue, class Predictor>
static RunResult execute(const Graph& g, uint32_t source, const Predictor& predictor,
                         uint64_t seed, uint64_t universe, std::vector<double>* samples = nullptr) {
    Queue q(seed, universe);
    RunResult result; result.distances.assign(g.adj.size(), INF);
    std::unordered_map<double, std::vector<uint32_t>> pending;
    auto add = [&](double key, uint32_t vertex, bool initial) {
        pending[key].push_back(vertex); q.push(key, predictor(key));
        if constexpr (Collect) { if (!initial) samples->push_back(key); }
        if constexpr (Observe) {
            ++result.insertions;
            result.peak_entries = std::max(result.peak_entries, uint64_t(q.live_entries()));
            result.peak_nodes = std::max(result.peak_nodes, uint64_t(q.live_nodes()));
        }
    };
    result.distances[source] = 0; add(0, source, true);
    while (!q.empty()) {
        const double key = q.pop();
        auto it = pending.find(key);
        require(it != pending.end() && !it->second.empty(), "queue returned missing pending key");
        const uint32_t u = it->second.back(); it->second.pop_back();
        if (it->second.empty()) pending.erase(it);
        if constexpr (Observe) ++result.extractions;
        if (key > result.distances[u]) continue;
        for (const Edge& edge : g.adj[u]) {
            const double next = key + edge.weight;
            require(std::isfinite(next), "distance overflow");
            if (next < result.distances[edge.to]) {
                result.distances[edge.to] = next; add(next, edge.to, false);
            }
        }
    }
    result.final_entries = q.live_entries(); result.final_pending = pending.size();
    return result;
}
struct Models {
    std::unique_ptr<ExactPrediction> exact;
    std::unique_ptr<LinearPrediction> linear;
    std::unique_ptr<HistogramPrediction> hist;
    uint64_t universe = 1;
};
static const std::vector<std::string> DEFAULT_METHODS = {
    "direct_heap", "std_heap", "std_multiset", "grouped_heap", "plain_skiplist",
    "guided_set_exact", "guided_set_linear", "guided_set_hist64",
    "guided_bitmap_exact", "guided_bitmap_linear", "guided_bitmap_hist64"
};
template<bool Observe>
static RunResult run_method(const std::string& name, const Graph& graph, uint32_t source,
                            const Models& model, uint64_t seed) {
    NoPrediction none;
    if (name == "direct_heap") return execute_direct_heap<Observe, false>(graph, source);
    if (name == "std_heap") return execute<Observe, false, HeapQueue>(graph, source, none, seed, 1);
    if (name == "std_multiset") return execute<Observe, false, MultisetQueue>(graph, source, none, seed, 1);
    if (name == "grouped_heap") return execute<Observe, false, GroupedHeapQueue>(graph, source, none, seed, 1);
    if (name == "plain_skiplist") return execute<Observe, false, SkipQueue<SetIndex, false>>(
        graph, source, none, seed, 1);
    if (name == "radix") return execute<Observe, false, RadixQueue>(graph, source, none, seed, 1);
    if (name == "guided_set_exact") return execute<Observe, false, SkipQueue<SetIndex, true>>(
        graph, source, *model.exact, seed, model.universe);
    if (name == "guided_set_linear") return execute<Observe, false, SkipQueue<SetIndex, true>>(
        graph, source, *model.linear, seed, model.universe);
    if (name == "guided_set_hist64") return execute<Observe, false, SkipQueue<SetIndex, true>>(
        graph, source, *model.hist, seed, model.universe);
    if (name == "guided_bitmap_exact") return execute<Observe, false, SkipQueue<BitmapIndex, true>>(
        graph, source, *model.exact, seed, model.universe);
    if (name == "guided_bitmap_linear") return execute<Observe, false, SkipQueue<BitmapIndex, true>>(
        graph, source, *model.linear, seed, model.universe);
    if (name == "guided_bitmap_hist64") return execute<Observe, false, SkipQueue<BitmapIndex, true>>(
        graph, source, *model.hist, seed, model.universe);
    throw std::runtime_error("unknown method: " + name);
}
static uint64_t distance_hash(const std::vector<double>& distances) {
    // FNV-1a over explicitly little-endian IEEE754 words. Not a cryptographic hash.
    static_assert(sizeof(double) == 8 && std::numeric_limits<double>::is_iec559,
                  "requires IEEE754 binary64");
    uint64_t h = 14695981039346656037ULL;
    for (double value : distances) {
        uint64_t word; std::memcpy(&word, &value, 8);
        for (unsigned i = 0; i < 8; ++i) { h ^= (word >> (8 * i)) & 255; h *= 1099511628211ULL; }
    }
    return h;
}
static void check_result(const RunResult& result, const std::vector<double>& reference) {
    require(result.distances == reference, "exact distance vector mismatch");
    require(result.final_entries == 0 && result.final_pending == 0, "queue did not fully drain");
    if (result.insertions) require(result.insertions == result.extractions, "insert/extract imbalance");
}
static void write_distances(const std::string& path, const std::vector<double>& distances) {
    if (path.empty()) return;
    std::ofstream f(path, std::ios::binary); require(bool(f), "cannot write distances");
    for (double value : distances) {
        uint64_t word; std::memcpy(&word, &value, 8);
        for (unsigned i = 0; i < 8; ++i) f.put(char((word >> (8 * i)) & 255));
    }
    require(bool(f), "distance output failed");
}

template<class Queue, class Ranker>
static uint64_t random_queue_test(uint64_t seed, Ranker ranker, bool monotone_inserts = false) {
    Queue q(seed, 256); std::multiset<double> reference; std::mt19937_64 rng(seed + 1000);
    uint64_t checks = 0; double last = 0;
    for (size_t step = 0; step < 2000; ++step) {
        if (reference.empty() || rng() % 100 < 62) {
            const double key = monotone_inserts ? last + double(rng() % 128) : double(rng() % 128);
            q.push(key, ranker(key)); reference.insert(key);
        } else {
            const double expected = *reference.begin(); reference.erase(reference.begin());
            last = q.pop(); require(last == expected, "random queue trace mismatch"); ++checks;
        }
        require(q.live_entries() == reference.size() && q.empty() == reference.empty(), "queue size mismatch");
    }
    while (!reference.empty()) {
        require(q.pop() == *reference.begin(), "queue drain order mismatch");
        reference.erase(reference.begin()); ++checks;
    }
    require(q.empty() && q.live_entries() == 0, "queue drain mismatch"); return checks;
}
static void selftest() {
    uint64_t index_checks = 0, queue_checks = 0, graph_checks = 0;
    std::mt19937_64 rng(874231);
    for (uint64_t universe : {1ULL, 2ULL, 63ULL, 64ULL, 65ULL, 4095ULL, 4096ULL, 4097ULL, 100003ULL}) {
        BitmapIndex bitmap(universe); SetIndex tree(universe); std::set<Rank> reference;
        for (size_t step = 0; step < 10000; ++step) {
            const Rank key = Rank(rng() % universe);
            if (rng() & 1) {
                const bool inserted = reference.insert(key).second;
                require(bitmap.add(key) == inserted && tree.add(key) == inserted, "index insertion mismatch");
            } else {
                const bool erased = reference.erase(key) != 0;
                require(bitmap.discard(key) == erased && tree.discard(key) == erased, "index deletion mismatch");
            }
            const uint64_t query = rng() % (universe + 2);
            auto it = reference.lower_bound(Rank(query)); std::optional<Rank> expected;
            if (it != reference.begin()) expected = *--it;
            require(bitmap.predecessor_strict(query) == expected && tree.predecessor_strict(query) == expected,
                    "strict predecessor mismatch");
            require(bitmap.size() == reference.size() && tree.size() == reference.size(), "index size mismatch");
            ++index_checks;
        }
    }
    for (uint64_t seed = 1; seed <= 40; ++seed) {
        const auto identity = [](double x) { return Rank(uint64_t(x) % 256); };
        const auto reverse = [](double x) { return Rank(255 - uint64_t(x) % 256); };
        const auto collision = [](double x) { return Rank(uint64_t(x) / 16); };
        const auto constant = [](double) { return Rank(0); };
        queue_checks += random_queue_test<HeapQueue>(seed, constant);
        queue_checks += random_queue_test<MultisetQueue>(seed, constant);
        queue_checks += random_queue_test<GroupedHeapQueue>(seed, constant);
        queue_checks += random_queue_test<SkipQueue<SetIndex, false>>(seed, constant);
        queue_checks += random_queue_test<RadixQueue>(seed, constant, true);
        for (auto ranker : {+identity, +reverse, +collision, +constant}) {
            queue_checks += random_queue_test<SkipQueue<SetIndex, true>>(seed, ranker);
            queue_checks += random_queue_test<SkipQueue<BitmapIndex, true>>(seed, ranker);
            // Inspect every pointer and bucket after each operation, separately.
            SkipQueue<BitmapIndex, true> checked(seed, 256); std::multiset<double> values;
            for (size_t i = 0; i < 150; ++i) {
                if (values.empty() || rng() % 3) {
                    double key = double(rng() % 40); checked.push(key, ranker(key)); values.insert(key);
                } else { require(checked.pop() == *values.begin(), "checked queue mismatch"); values.erase(values.begin()); }
                checked.validate();
            }
            while (!values.empty()) { require(checked.pop() == *values.begin(), "checked drain");
                values.erase(values.begin()); checked.validate(); }
        }
    }
    for (uint64_t trial = 0; trial < 80; ++trial) {
        Graph g; g.adj.resize(size_t(2 + rng() % 30));
        for (size_t u = 0; u < g.adj.size(); ++u) {
            for (unsigned j = 0; j < 5; ++j) {
                const uint32_t v = uint32_t(rng() % g.adj.size());
                // Zero and duplicate edges, disconnected graphs, self loops.
                const double weight = double(rng() % 12);
                g.adj[u].push_back({v, weight}); ++g.edges;
            }
        }
        const uint32_t train = uint32_t(rng() % g.adj.size());
        const uint32_t source = uint32_t(rng() % g.adj.size());
        auto reference = oracle(g, source); require(reference == bellman_ford(g, source), "oracle/BF mismatch");
        std::vector<double> samples;
        auto training = execute<true, true, HeapQueue>(g, train, NoPrediction{}, trial, 1, &samples);
        check_result(training, oracle(g, train));
        Models models; models.universe = samples.size() + 1;
        models.exact = std::make_unique<ExactPrediction>(samples);
        models.linear = std::make_unique<LinearPrediction>(samples);
        models.hist = std::make_unique<HistogramPrediction>(samples);
        for (double scale : {1.0, 0.25, 4.0, 1.1}) {
            Graph target = g;
            for (auto& edges : target.adj) for (auto& edge : edges) edge.weight *= scale;
            // Freeze advisors trained on the unscaled graph, including saturation.
            const auto target_reference = oracle(target, source);
            // BF may follow a different order of floating additions. Integer and
            // dyadic cases permit exact oracle equality; fractional cases use the
            // independent pair-heap oracle with identical arithmetic semantics.
            if (scale != 1.1) require(target_reference == bellman_ford(target, source), "shifted oracle/BF mismatch");
            std::vector<double> shared_trace, direct_trace;
            const auto shared_result = execute<true, true, HeapQueue>(target, source,
                NoPrediction{}, trial + 17, 1, &shared_trace);
            const auto direct_result = execute_direct_heap<true, true>(target, source, &direct_trace);
            check_result(shared_result, target_reference); check_result(direct_result, target_reference);
            require(shared_trace == direct_trace, "direct/shared heap inserted-key sequence mismatch");
            require(shared_result.insertions == direct_result.insertions &&
                    shared_result.extractions == direct_result.extractions &&
                    shared_result.peak_entries == direct_result.peak_entries,
                    "direct/shared heap operation count mismatch");
            auto methods = DEFAULT_METHODS;
            if (scale == 1.0 || scale == 4.0) methods.push_back("radix");
            for (const auto& method : methods) {
                auto observed = run_method<true>(method, target, source, models, trial + 17);
                auto plain = run_method<false>(method, target, source, models, trial + 17);
                check_result(observed, target_reference); check_result(plain, target_reference); ++graph_checks;
            }
        }
    }
    std::cout << "{\"selftest\":\"passed\",\"index_mutations\":" << index_checks
              << ",\"queue_extractions\":" << queue_checks << ",\"graph_method_pairs\":"
              << graph_checks << ",\"direct_shared_trace_pairs\":320}\n";
}

struct Options {
    std::string graph, train_graph, output = "native_results", distances_output;
    uint32_t train = 0, source = 0;
    unsigned repeat = 7;
    uint64_t seed = 20261106;
    bool selftest = false, explicit_methods = false;
    std::vector<std::string> methods = DEFAULT_METHODS;
};
static Options options(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string flag = argv[i];
        if (flag == "--selftest") { o.selftest = true; continue; }
        if (flag == "--help") {
            std::cout << "--selftest OR --graph PATH [--train-graph PATH] --train ID --source ID "
                         "[--repeat 7] [--seed 20261106] [--output PREFIX] "
                         "[--distances-output PATH] [--methods comma,separated]\n";
            std::exit(0);
        }
        require(i + 1 < argc, "missing value for " + flag); const std::string value = argv[++i];
        if (flag == "--graph") o.graph = value;
        else if (flag == "--train-graph") o.train_graph = value;
        else if (flag == "--output") o.output = value;
        else if (flag == "--distances-output") o.distances_output = value;
        else if (flag == "--train" || flag == "--source") {
            const uint64_t id = std::stoull(value); require(id <= UINT32_MAX, "source id too large");
            if (flag == "--train") o.train = uint32_t(id); else o.source = uint32_t(id);
        } else if (flag == "--repeat") {
            const uint64_t n = std::stoull(value); require(n > 0 && n <= 100000, "invalid repetitions");
            o.repeat = unsigned(n);
        } else if (flag == "--seed") o.seed = std::stoull(value);
        else if (flag == "--methods") {
            o.explicit_methods = true; o.methods.clear(); std::istringstream stream(value); std::string m;
            while (std::getline(stream, m, ',')) { require(!m.empty(), "empty method"); o.methods.push_back(m); }
            require(!o.methods.empty(), "empty method list");
        } else throw std::runtime_error("unknown option: " + flag);
    }
    if (o.train_graph.empty()) o.train_graph = o.graph;
    return o;
}
struct TimingRecord {
    std::string method; unsigned repetition, order; uint64_t height_seed;
    double wall, cpu; uint64_t hash;
};
static void benchmark(const Options& o) {
    require(!o.graph.empty(), "--graph is required");
    const Graph graph = read_graph(o.graph);
    std::optional<Graph> separate_training;
    if (o.train_graph != o.graph) separate_training.emplace(read_graph(o.train_graph));
    const Graph& training_graph = separate_training ? *separate_training : graph;
    require(o.source < graph.adj.size() && o.train < training_graph.adj.size(), "source outside graph");
    const bool radix_eligible = graph.integral &&
        static_cast<long double>(graph.adj.size()) * graph.max_weight < 9007199254740992.0L;
    auto methods = o.methods;
    if (!o.explicit_methods && radix_eligible) methods.push_back("radix");
    std::set<std::string> unique_methods;
    for (const auto& method : methods) {
        require(unique_methods.insert(method).second, "duplicate method");
        require(method != "radix" || radix_eligible, "radix requested for ineligible graph");
        require(method == "radix" || std::find(DEFAULT_METHODS.begin(), DEFAULT_METHODS.end(), method) != DEFAULT_METHODS.end(),
                "unknown method: " + method);
    }
    const auto reference = oracle(graph, o.source);
    const auto train_reference = oracle(training_graph, o.train);
    std::vector<double> samples;
    Stopwatch train_watch;
    auto training = execute<false, true, HeapQueue>(training_graph, o.train, NoPrediction{}, o.seed, 1, &samples);
    const auto train_time = train_watch.seconds(); check_result(training, train_reference);
    require(samples.size() < uint64_t(UINT32_MAX), "too many training keys for rank type");
    Models models; models.universe = samples.size() + 1;
    std::map<std::string, std::pair<double, double>> build_times;
    { Stopwatch sw; models.exact = std::make_unique<ExactPrediction>(samples); build_times["exact"] = sw.seconds(); }
    { Stopwatch sw; models.linear = std::make_unique<LinearPrediction>(samples); build_times["linear"] = sw.seconds(); }
    { Stopwatch sw; models.hist = std::make_unique<HistogramPrediction>(samples); build_times["hist64"] = sw.seconds(); }
    // Diagnostic validation run is outside timers, and also warms each method once.
    std::map<std::string, RunResult> diagnostics;
    std::vector<std::string> warmup_order = methods;
    std::mt19937_64 order_rng(o.seed ^ 0x6d617463686564ULL);
    std::shuffle(warmup_order.begin(), warmup_order.end(), order_rng);
    std::optional<std::array<uint64_t, 3>> shared_work;
    for (const auto& method : warmup_order) {
        auto result = run_method<true>(method, graph, o.source, models, o.seed);
        check_result(result, reference);
        const std::array<uint64_t, 3> work{result.insertions, result.extractions, result.peak_entries};
        if (shared_work) require(work == *shared_work, "method changed shared LIFO work counts");
        else shared_work = work;
        result.distances.clear(); result.distances.shrink_to_fit();
        diagnostics.emplace(method, std::move(result));
    }
    const uint64_t expected_hash = distance_hash(reference);
    write_distances(o.distances_output, reference);
    std::ofstream csv(o.output + ".csv"); require(bool(csv), "cannot open CSV output");
    csv << "method,source,train,repetition,order,height_seed,wall_seconds,cpu_seconds,distance_fnv1a64,correct,drained\n";
    csv << std::setprecision(17);
    std::vector<TimingRecord> records;
    for (unsigned repetition = 0; repetition < o.repeat; ++repetition) {
        auto order = methods; std::shuffle(order.begin(), order.end(), order_rng);
        const uint64_t height_seed = o.seed + uint64_t(repetition + 1) * 0x9e3779b97f4a7c15ULL;
        for (size_t i = 0; i < order.size(); ++i) {
            Stopwatch sw;
            auto result = run_method<false>(order[i], graph, o.source, models, height_seed);
            const auto elapsed = sw.seconds(); // Verification/hash/destruction of distances are outside.
            check_result(result, reference); const uint64_t hash = distance_hash(result.distances);
            require(hash == expected_hash, "distance checksum mismatch");
            records.push_back({order[i], repetition, unsigned(i), height_seed, elapsed.first, elapsed.second, hash});
            csv << order[i] << ',' << o.source << ',' << o.train << ',' << repetition << ',' << i << ','
                << height_seed << ',' << elapsed.first << ',' << elapsed.second << ',' << hash << ",true,true\n";
            csv.flush(); require(bool(csv), "CSV output failed");
        }
    }
    std::ofstream json(o.output + ".json"); require(bool(json), "cannot open JSON output");
    json << std::setprecision(17);
    json << "{\n  \"schema_version\":1,\n  \"implementation\":\"independent engineered C++17; grouped live-only skiplist\",\n"
         << "  \"graph\":" << quote(o.graph) << ",\n  \"train_graph\":" << quote(o.train_graph)
         << ",\n  \"vertices\":" << graph.adj.size() << ",\n  \"arcs\":" << graph.edges
         << ",\n  \"train_vertices\":" << training_graph.adj.size() << ",\n  \"train_arcs\":" << training_graph.edges
         << ",\n  \"source\":" << o.source << ",\n  \"train\":" << o.train << ",\n  \"repeat\":" << o.repeat
         << ",\n  \"seed\":" << o.seed << ",\n  \"radix_eligible\":" << (radix_eligible ? "true" : "false")
         << ",\n  \"training_keys\":" << samples.size() << ",\n  \"rank_universe\":" << models.universe
         << ",\n  \"training_wall_seconds\":" << train_time.first << ",\n  \"training_cpu_seconds\":" << train_time.second
         << ",\n  \"training_timing_note\":\"one common std_heap run, collecting every successful noninitial insertion; excludes graph read and oracle\",\n"
         << "  \"clock_ticks_per_second\":" << CLOCKS_PER_SEC
         << ",\n  \"compiler\":" << quote(__VERSION__)
         << ",\n  \"distance_fnv1a64\":" << quote(std::to_string(expected_hash))
         << ",\n  \"distances_output\":" << quote(o.distances_output)
         << ",\n  \"distance_encoding\":\"little-endian IEEE754 binary64, vertex order\",\n"
         << "  \"all_exact_distances_passed\":true,\n  \"all_queues_drained\":true,\n"
         << "  \"common_lifo_work_counts_passed\":true,\n"
         << "  \"timed_records\":" << records.size() << ",\n  \"methods\":[";
    for (size_t i = 0; i < methods.size(); ++i) { if (i) json << ','; json << quote(methods[i]); }
    json << "],\n  \"build_times\":{";
    bool comma = false;
    for (const auto& item : build_times) {
        if (comma) json << ','; comma = true;
        json << quote(item.first) << ":{\"wall_seconds\":" << item.second.first
             << ",\"cpu_seconds\":" << item.second.second << '}';
    }
    json << "},\n  \"diagnostics_outside_timing\":{"; comma = false;
    for (const auto& item : diagnostics) {
        if (comma) json << ','; comma = true;
        json << quote(item.first) << ":{\"insertions\":" << item.second.insertions
             << ",\"extractions\":" << item.second.extractions
             << ",\"peak_entries\":" << item.second.peak_entries
             << ",\"peak_queue_nodes\":" << item.second.peak_nodes << '}';
    }
    json << "},\n  \"notes\":["
         << "\"Times include queue construction/destruction, distance allocation, predictor queries and all queue work. All methods except direct_heap include the common pending map. Model training/build and correctness checks are excluded.\","
         << "\"Observed counters are compiled out of timed runs. Queue multiplicities and size fields remain algorithm state.\","
         << "\"One observed warmup per method, shuffled; each repetition has matched height seed and shuffled method order.\","
         << "\"Same scalar distance to LIFO vertex map for queue ablations. direct_heap embeds vertices with reverse insertion sequence, preserving LIFO ties without the map. Reference oracle uses an independent vertex-tie pair heap.\","
         << "\"Guided set and bitmap share the same grouped skiplist and live-only rank buckets; plain skiplist shares grouping without rank buckets.\","
         << "\"Grouping alters original artifact hint history and random-height consumption. This is an engineered crosscheck, not exact artifact reproduction.\","
         << "\"Dense hierarchical bitmap is O(log_64 U), not vEB. Grouped heap controls duplicate compression. No RSS or total memory claim.\","
         << "\"All three fitted advisors and training samples remain resident across methods; this is not isolated per-method memory measurement.\","
         << "\"Training/build are measured once per invocation; baseline queues require neither. Shared-host timings are diagnostic.\""
         << "]\n}\n";
    require(bool(json), "JSON output failed");
    std::cout << "{\"status\":\"passed\",\"records\":" << records.size()
              << ",\"csv\":" << quote(o.output + ".csv") << ",\"json\":" << quote(o.output + ".json") << "}\n";
}
int main(int argc, char** argv) {
    try {
        const auto o = options(argc, argv);
        if (o.selftest) selftest(); else benchmark(o);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "{\"status\":\"failed\",\"error\":" << quote(e.what()) << "}\n";
        return 1;
    }
}
