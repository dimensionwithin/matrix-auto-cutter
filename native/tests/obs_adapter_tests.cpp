#include "matrix_auto_cutter/obs_adapter.hpp"

#include <chrono>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using matrix_auto_cutter::CallbackResult;
using matrix_auto_cutter::CalibrationSnapshot;
using matrix_auto_cutter::EventSnapshot;
using matrix_auto_cutter::JournalSnapshot;
using matrix_auto_cutter::ProducerResult;
using matrix_auto_cutter::RecordingStart;
using matrix_auto_cutter::RecordingStop;
using namespace matrix_auto_cutter::obs_adapter;

struct TestFailure final : std::runtime_error {
    using std::runtime_error::runtime_error;
};

void check(const bool condition, const std::string_view message) {
    if (!condition) {
        throw TestFailure(std::string(message));
    }
}

template <typename Predicate>
void wait_for(Predicate predicate, const std::string_view message) {
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!predicate()) {
        if (std::chrono::steady_clock::now() >= deadline) {
            throw TestFailure(std::string(message));
        }
        std::this_thread::sleep_for(2ms);
    }
}

struct ProducerState final {
    std::mutex mutex;
    ProducerResult start_result{ProducerResult::producer_ok};
    CallbackResult calibration_result{CallbackResult::accepted};
    ProducerResult stop_result{ProducerResult::producer_ok};
    ProducerResult shutdown_result{ProducerResult::producer_ok};
    unsigned starts{};
    unsigned recording_started_events{};
    unsigned calibrations{};
    unsigned stops{};
    unsigned shutdowns{};
    std::uint64_t final_frames{};
    std::uint64_t final_ns{};
    std::uint64_t recording_started_ns{};
    std::uint64_t recording_started_frames{};
    std::string start_path;
    std::string stop_path;
};

class FakeProducer final : public ProducerPort {
  public:
    explicit FakeProducer(std::shared_ptr<ProducerState> state) : state_(std::move(state)) {}

    ProducerResult start_recording(const RecordingStart& start) noexcept override {
        std::lock_guard lock(state_->mutex);
        ++state_->starts;
        state_->start_path = start.recording_path_utf8;
        return state_->start_result;
    }

    CallbackResult submit(JournalSnapshot snapshot) noexcept override {
        std::lock_guard lock(state_->mutex);
        if (std::holds_alternative<EventSnapshot>(snapshot)) {
            ++state_->recording_started_events;
            state_->recording_started_ns = std::get<EventSnapshot>(snapshot).clock.monotonic_ns;
            state_->recording_started_frames =
                std::get<EventSnapshot>(snapshot).clock.output_frame_count;
            return CallbackResult::accepted;
        }
        ++state_->calibrations;
        return state_->calibration_result;
    }

    ProducerResult normal_stop(const RecordingStop& stop) noexcept override {
        std::lock_guard lock(state_->mutex);
        ++state_->stops;
        state_->final_frames = stop.clock.output_frame_count;
        state_->final_ns = stop.clock.monotonic_ns;
        state_->stop_path = stop.recording_path_utf8;
        return state_->stop_result;
    }

    ProducerResult shutdown() noexcept override {
        std::lock_guard lock(state_->mutex);
        ++state_->shutdowns;
        return state_->shutdown_result;
    }

    ProducerResult result() const noexcept override {
        std::lock_guard lock(state_->mutex);
        return state_->shutdown_result;
    }

    std::string recording_session_id() const noexcept override {
        return "11111111-1111-4111-8111-111111111111";
    }

  private:
    std::shared_ptr<ProducerState> state_;
};

class FakeFactory final : public ProducerFactory {
  public:
    explicit FakeFactory(std::shared_ptr<ProducerState> state) : state_(std::move(state)) {}

    std::unique_ptr<ProducerPort> create() noexcept override {
        ++creates;
        return std::make_unique<FakeProducer>(state_);
    }

    std::atomic<unsigned> creates{};

  private:
    std::shared_ptr<ProducerState> state_;
};

class FakeHost final : public AdapterHost {
  public:
    bool install_callbacks(
        const FrontendCallback frontend,
        const TickCallback tick,
        void* private_data) noexcept override {
        frontend_ = frontend;
        tick_ = tick;
        private_data_ = private_data;
        installed = true;
        return install_result;
    }

    void remove_callbacks() noexcept override {
        installed = false;
        removed = true;
        frontend_ = nullptr;
        tick_ = nullptr;
        private_data_ = nullptr;
    }

    bool acquire_recording_start(RecordingSignal& signal) noexcept override {
        if (!capture_result) {
            return false;
        }
        ++references;
        signal = next_signal;
        return true;
    }

    bool capture_recording_stop(RecordingSignal& signal) noexcept override {
        if (!capture_result || references.load() == 0) {
            return false;
        }
        signal = next_signal;
        return true;
    }

    bool capture_clock(
        std::uint64_t& absolute_monotonic_ns,
        std::uint64_t& output_frame_count) noexcept override {
        if (!capture_result || references.load() == 0) {
            return false;
        }
        absolute_monotonic_ns = next_signal.absolute_monotonic_ns;
        output_frame_count = next_signal.output_frame_count;
        return true;
    }

    void release_recording_output() noexcept override {
        unsigned current = references.load();
        while (current > 0 && !references.compare_exchange_weak(current, current - 1)) {
        }
    }

    std::string_view obs_version() const noexcept override { return "32.1.2"; }

    void log(LogLevel, const std::string_view message) noexcept override {
        try {
            std::lock_guard lock(log_mutex);
            logs.emplace_back(message);
        } catch (...) {
        }
    }

    void set_signal(
        const std::string_view path,
        const std::uint64_t absolute_ns,
        const std::uint64_t frames) {
        check(next_signal.path.assign(path), "test path was not bounded");
        check(next_signal.output_id.assign("ffmpeg_muxer"), "test output id was not bounded");
        next_signal.absolute_monotonic_ns = absolute_ns;
        next_signal.output_frame_count = frames;
        next_signal.fragmented_mp4 = false;
    }

    void fire(const FrontendEvent event) noexcept {
        if (frontend_ != nullptr) {
            frontend_(event, private_data_);
        }
    }

    void tick() noexcept {
        if (tick_ != nullptr) {
            tick_(private_data_);
        }
    }

    bool install_result{true};
    bool capture_result{true};
    bool installed{};
    bool removed{};
    std::atomic<unsigned> references{};
    RecordingSignal next_signal{};
    std::mutex log_mutex;
    std::vector<std::string> logs;

  private:
    FrontendCallback frontend_{};
    TickCallback tick_{};
    void* private_data_{};
};

AdapterOptions test_options() {
    AdapterOptions options;
    auto ids = std::make_shared<unsigned>(0);
    options.uuid_factory = [ids] {
        ++*ids;
        return *ids % 2 == 1 ? "22222222-2222-4222-8222-222222222222"
                             : "33333333-3333-4333-8333-333333333333";
    };
    return options;
}

void start_normal(FakeHost& host, ObsJournalAdapter& adapter) {
    host.set_signal(R"(P:\smoke\real-obs.mp4)", 10'000'000'000ULL, 7);
    host.fire(FrontendEvent::recording_started);
    wait_for([&] { return adapter.state() == AdapterState::active; }, "adapter did not activate");
}

void test_module_initialization_normal_lifecycle_and_cleanup() {
    auto producer_state = std::make_shared<ProducerState>();
    FakeFactory factory(producer_state);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options());
    check(adapter.load(), "module adapter load failed");
    check(host.installed, "callbacks were not installed");

    start_normal(host, adapter);
    {
        std::lock_guard lock(producer_state->mutex);
        check(producer_state->starts == 1, "producer did not start exactly once");
        check(producer_state->recording_started_events == 0,
              "recording_started was submitted before the output counter advanced");
    }

    host.set_signal(R"(P:\smoke\real-obs.mp4)", 12'000'000'000ULL, 127);
    host.tick();
    host.set_signal(R"(P:\smoke\real-obs.mp4)", 13'000'000'000ULL, 187);
    host.tick();
    {
        std::lock_guard lock(producer_state->mutex);
        check(producer_state->calibrations == 1, "calibration cadence was not approximately two seconds");
        check(producer_state->recording_started_events == 1,
              "recording_started was not submitted exactly once");
        check(producer_state->recording_started_ns == 0,
              "output frame/QPC anchor changed the expected start timestamp");
        check(producer_state->recording_started_frames == 8,
              "recording_started did not bind the first actual output frame counter");
    }

    host.set_signal(R"(P:\smoke\real-obs.mp4)", 14'000'000'000ULL, 247);
    host.fire(FrontendEvent::recording_stopped);
    wait_for([&] { return adapter.state() == AdapterState::idle; }, "normal stop did not finish");
    const auto report = adapter.last_report();
    check(report.has_value() && report->successful, "normal run was not reported successful");
    check(report->final_frame_count == 247, "final frame counter changed");
    check(report->calibration_count == 1, "calibration count changed");
    check(report->recording_session_id == "11111111-1111-4111-8111-111111111111",
          "session id was not exposed");
    check(report->journal_path_utf8.ends_with(".recording-journal.ndjson"),
          "journal path was not discoverable");
    {
        std::lock_guard lock(producer_state->mutex);
        check(producer_state->stops == 1 && producer_state->shutdowns == 1,
              "normal stop/shutdown counts differ");
        check(producer_state->final_frames == 247, "producer did not receive final frame count");
        check(producer_state->final_ns == 3'983'333'333ULL,
              "stop did not derive final QPC from the bounded start counter anchor");
    }

    host.tick();
    {
        std::lock_guard lock(producer_state->mutex);
        check(producer_state->calibrations == 1, "callback after stop was accepted");
    }
    host.fire(FrontendEvent::recording_stopped);
    adapter.unload();
    adapter.unload();
    check(host.removed && !host.installed, "callbacks were not removed");
    check(host.references.load() == 0, "recording output reference leaked");
}

void test_concurrent_start_and_non_mp4_fail_closed() {
    auto producer_state = std::make_shared<ProducerState>();
    FakeFactory factory(producer_state);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options());
    check(adapter.load(), "load failed");

    host.set_signal(R"(P:\smoke\not-supported.mkv)", 1, 0);
    host.fire(FrontendEvent::recording_started);
    check(adapter.state() == AdapterState::idle, "non-MP4 changed adapter state");
    check(factory.creates.load() == 0, "non-MP4 created a producer");
    check(host.references.load() == 0, "non-MP4 reference leaked");

    host.set_signal(R"(P:\smoke\hybrid.mp4)", 1, 0);
    check(host.next_signal.output_id.assign("mp4_output"), "hybrid output id was not bounded");
    host.fire(FrontendEvent::recording_started);
    check(adapter.state() == AdapterState::idle, "hybrid MP4 changed adapter state");
    check(factory.creates.load() == 0, "hybrid MP4 created a producer");
    check(host.references.load() == 0, "hybrid MP4 reference leaked");

    host.set_signal(R"(P:\smoke\fragmented.mp4)", 1, 0);
    host.next_signal.fragmented_mp4 = true;
    host.fire(FrontendEvent::recording_started);
    check(adapter.state() == AdapterState::idle, "fragmented MP4 changed adapter state");
    check(factory.creates.load() == 0, "fragmented MP4 created a producer");
    check(host.references.load() == 0, "fragmented MP4 reference leaked");

    start_normal(host, adapter);
    host.set_signal(R"(P:\smoke\real-obs.mp4)", 10'100'000'000ULL, 8);
    host.tick();
    host.fire(FrontendEvent::recording_started);
    check(factory.creates.load() == 1, "concurrent start created a second producer");
    check(host.references.load() == 1, "concurrent start reference was not released");
    host.set_signal(R"(P:\smoke\real-obs.mp4)", 11'000'000'000ULL, 67);
    host.fire(FrontendEvent::recording_stopped);
    wait_for([&] { return adapter.state() == AdapterState::idle; }, "cleanup stop failed");
    adapter.unload();
}

void test_overflow_and_io_failure_are_not_success() {
    {
        auto producer_state = std::make_shared<ProducerState>();
        producer_state->calibration_result = CallbackResult::full;
        producer_state->stop_result = ProducerResult::producer_failed_queue_overflow;
        producer_state->shutdown_result = ProducerResult::producer_failed_queue_overflow;
        FakeFactory factory(producer_state);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options());
        check(adapter.load(), "overflow adapter load failed");
        start_normal(host, adapter);
        host.set_signal(R"(P:\smoke\real-obs.mp4)", 12'000'000'000ULL, 127);
        host.tick();
        host.set_signal(R"(P:\smoke\real-obs.mp4)", 12'100'000'000ULL, 133);
        host.tick();
        host.set_signal(R"(P:\smoke\real-obs.mp4)", 13'000'000'000ULL, 187);
        host.fire(FrontendEvent::recording_stopped);
        wait_for([&] { return adapter.state() == AdapterState::failed; }, "overflow did not fail");
        const auto report = adapter.last_report();
        check(report.has_value() && !report->successful &&
                  report->result == ProducerResult::producer_failed_queue_overflow,
              "overflow was reported as success");
        adapter.unload();
    }
    {
        auto producer_state = std::make_shared<ProducerState>();
        producer_state->start_result = ProducerResult::producer_failed_io;
        producer_state->shutdown_result = ProducerResult::producer_failed_io;
        FakeFactory factory(producer_state);
        FakeHost host;
        ObsJournalAdapter adapter(host, factory, test_options());
        check(adapter.load(), "IO adapter load failed");
        host.set_signal(R"(P:\smoke\io.mp4)", 1, 0);
        host.fire(FrontendEvent::recording_started);
        wait_for([&] { return adapter.state() == AdapterState::failed; }, "IO failure did not fail");
        const auto report = adapter.last_report();
        check(report.has_value() && !report->successful, "IO failure was reported as success");
        check(host.references.load() == 0, "IO failure leaked output reference");
        adapter.unload();
    }
}

void test_no_exception_crosses_boundaries() {
    ObsJournalAdapter::frontend_boundary(FrontendEvent::recording_started, nullptr);
    ObsJournalAdapter::tick_boundary(nullptr);
    auto producer_state = std::make_shared<ProducerState>();
    FakeFactory factory(producer_state);
    FakeHost host;
    ObsJournalAdapter adapter(host, factory, test_options());
    check(adapter.load(), "boundary adapter load failed");
    host.capture_result = false;
    host.fire(FrontendEvent::recording_started);
    host.fire(FrontendEvent::recording_stopped);
    host.tick();
    adapter.unload();
}

}  // namespace

int main() {
    const std::vector<std::pair<const char*, std::function<void()>>> tests{
        {"module/start/one-event/calibration/stop/post-stop/references",
         test_module_initialization_normal_lifecycle_and_cleanup},
        {"concurrent-start/non-mp4", test_concurrent_start_and_non_mp4_fail_closed},
        {"overflow/io-fail-closed", test_overflow_and_io_failure_are_not_success},
        {"no-exception-c-boundary", test_no_exception_crosses_boundaries},
    };
    int failures = 0;
    for (const auto& [name, test] : tests) {
        try {
            test();
            std::cout << "PASS " << name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL " << name << ": " << error.what() << '\n';
        } catch (...) {
            ++failures;
            std::cerr << "FAIL " << name << ": unknown exception\n";
        }
    }
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
